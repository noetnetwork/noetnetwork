import gc
import itertools
import queue
import threading
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
from noet.admission import AdmissionBudget, AdmissionFull
from test_verifier_artifacts import extracted


class AdmissionTests(unittest.TestCase):
    def test_byte_and_count_caps_release_with_last_owner(self):
        budget=AdmissionBudget(24,2)
        first=budget.admit(b'a'*16);second=budget.admit(b'b'*8)
        with self.assertRaises(AdmissionFull):budget.admit(b'c'*8)
        mirrored={'_admission':first};del first;gc.collect()
        self.assertEqual(budget.snapshot(),{'bytes':24,'requests':2})
        del mirrored;gc.collect()
        self.assertEqual(budget.snapshot(),{'bytes':8,'requests':1})
        third=budget.admit(b'c'*8)
        with self.assertRaises(AdmissionFull):budget.admit(b'd'*8)
        del second,third;gc.collect()
        self.assertEqual(budget.snapshot(),{'bytes':0,'requests':0})

    def test_retry_shares_reservation_even_at_capacity(self):
        budget=AdmissionBudget(8,1);raw=b'a'*8
        first=budget.admit(raw);retry=budget.admit(raw,first)
        self.assertIs(first,retry)
        self.assertEqual(budget.snapshot()['requests'],1)
        with self.assertRaises(ValueError):budget.admit(b'b'*8,first)
        with self.assertRaises(ValueError):AdmissionBudget().admit(raw,first)

    def test_invalid_inputs_do_not_reserve(self):
        budget=AdmissionBudget()
        for raw in (None,b'',b'a'*7,'a'*8):
            with self.assertRaises(ValueError):budget.admit(raw)
        self.assertEqual(budget.snapshot(),{'bytes':0,'requests':0})
        for size,count in ((0,1),(8,0),(True,1),(8,True)):
            with self.assertRaises(ValueError):AdmissionBudget(size,count)

    def test_concurrent_admission_cannot_overbook(self):
        budget=AdmissionBudget(24,3)
        def admit(_):
            try:return budget.admit(b'a'*8)
            except AdmissionFull:return None
        with ThreadPoolExecutor(max_workers=16) as pool:
            results=list(pool.map(admit,range(100)))
        self.assertEqual(sum(value is not None for value in results),3)
        self.assertEqual(budget.snapshot(),{'bytes':24,'requests':3})
        del results;gc.collect()
        self.assertEqual(budget.snapshot(),{'bytes':0,'requests':0})

    def test_overload_does_not_parse_or_issue_failure_verdict(self):
        parser=Mock();budget=AdmissionBudget(8,1);held=budget.admit(b'a'*8)
        method=extracted('main.py','enqueue_request',{'AdmissionFull':AdmissionFull,'ValidationRequest':parser})
        obj=types.SimpleNamespace(admission=budget,logger=Mock(),send_error_response=Mock())
        method(obj,b'b'*8)
        parser.ValidationRequest.GetRootAs.assert_not_called()
        obj.send_error_response.assert_not_called()
        self.assertEqual(budget.snapshot()['requests'],1)

    def test_actual_full_and_mirrored_quick_share_one_reservation(self):
        class Request:
            def __init__(self,raw):self.raw=raw
            def HashIdAsNumpy(self):return types.SimpleNamespace(tobytes=lambda:b'h'*32)
            def ValidationType(self):return 3
        parser=types.SimpleNamespace(ValidationRequest=types.SimpleNamespace(GetRootAs=lambda raw,offset:Request(raw)))
        kinds=types.SimpleNamespace(ValidationType=types.SimpleNamespace(Quick=1,Quick_Smell=2,Full=3))
        method=extracted('main.py','enqueue_request',{'AdmissionFull':AdmissionFull,'ValidationRequest':parser,'ValidationType':kinds,'time':time})
        obj=types.SimpleNamespace(admission=AdmissionBudget(8,1),logger=Mock(),send_error_response=Mock(),
            status_lock=threading.RLock(),_queue_lock=threading.RLock(),full_req_lock=threading.RLock(),
            full_requested=set(),validation_status={},_request_ruleset={},_declared_ruleset=lambda req:1,
            _request_rules_tag=lambda req:'rules',is_phase_done=lambda *args:False,
            _enqueued={'quick':set(),'full':set()},_processing={'quick':set(),'full':set()},
            full_queue=queue.PriorityQueue(),quick_queue=queue.PriorityQueue(),_pq_counter=itertools.count())
        method(obj,b'a'*8)
        self.assertEqual(obj.full_queue.qsize(),1);self.assertEqual(obj.quick_queue.qsize(),1)
        full=obj.full_queue.get()[2];quick=obj.quick_queue.get()[2]
        self.assertIs(full['_admission'],quick['_admission'])
        del full;gc.collect();self.assertEqual(obj.admission.snapshot()['requests'],1)
        del quick;gc.collect();self.assertEqual(obj.admission.snapshot()['requests'],0)
        obj.send_error_response.assert_not_called()

    def test_idle_worker_releases_completed_request_before_waiting(self):
        budget=AdmissionBudget(8,1)
        class WorkQueue:
            def __init__(self):self.item=(0,0,{'hash_id':b'h'*32,'_admission':budget.admit(b'a'*8)})
            def get(self,timeout):
                if self.item is not None:
                    item=self.item;self.item=None;return item
                self_test.assertEqual(budget.snapshot(),{'bytes':0,'requests':0})
                obj.running=False
                raise queue.Empty
        self_test=self
        obj=types.SimpleNamespace(running=True,quick_queue=WorkQueue(),logger=Mock(),_queue_lock=threading.RLock(),
            _enqueued={'quick':{b'h'*32}},_processing={'quick':set()},validate_quick=lambda data,verifier:None)
        method=extracted('main.py','process_quick_validations',{'ProofVerifier':lambda:object(),'mca_set_enabled':lambda value:None,'queue':queue})
        method(obj)
        self.assertFalse(obj._processing['quick'])

    def test_actual_retry_reuses_capacity_and_preserves_request(self):
        raw=b'a'*8;budget=AdmissionBudget(8,1);held=budget.admit(raw)
        parser=types.SimpleNamespace(ValidationRequest=types.SimpleNamespace(GetRootAs=lambda data,offset:types.SimpleNamespace(raw=data)))
        kinds=types.SimpleNamespace(ValidationType=types.SimpleNamespace(Full=3))
        method=extracted('main.py','reenqueue_full_validation',{'ValidationRequest':parser,'ValidationType':kinds,'time':time})
        obj=types.SimpleNamespace(admission=budget,logger=Mock(),retry_lock=threading.RLock(),retry_counts={},
            _queue_lock=threading.RLock(),_enqueued={'full':set()},full_queue=queue.PriorityQueue(),_pq_counter=itertools.count())
        method(obj,b'h'*32,raw,1,admission=held)
        self.assertEqual(obj.full_queue.qsize(),1)
        data=obj.full_queue.get()[2]
        self.assertIs(data['_admission'],held);self.assertIs(data['raw_message'],raw)
        self.assertEqual(budget.snapshot(),{'bytes':8,'requests':1})
        del held,data;gc.collect()
        self.assertEqual(budget.snapshot()['requests'],0)
