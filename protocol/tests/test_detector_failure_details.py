import json
import time
import types
import unittest
import uuid
from unittest.mock import Mock
from test_verifier_artifacts import extracted


class NoVerdict(Exception):
    failure_type = 'detector_evidence_incomplete'


class DetectorFailureDetailsTests(unittest.TestCase):
    def helper(self):
        return extracted('detector_gate.py','incomplete_feature_details',{})

    def test_only_missing_eligible_positions_are_reported(self):
        record={'feature_refusals':[{'step':232,'reason':'union size 129 exceeds 128'},
            {'step':0,'reason':'unrelated'}, {'step':True,'reason':'invalid'}, None]}
        self.assertEqual(self.helper()(record,[233]),[{'position':233,'reason':'union size 129 exceeds 128'}])

    def test_messages_and_count_are_bounded(self):
        r={'feature_refusals':[{'step':i,'reason':'x\n\t'*1000} for i in range(256)]}
        details=self.helper()(r,list(range(1,257)))
        self.assertEqual(len(details),8)
        self.assertTrue(all(len(x['reason'])<=240 and '\n' not in x['reason'] and '\t' not in x['reason'] for x in details))
        for entries in (None,{},'not a list'):
            self.assertEqual(self.helper()({'feature_refusals':entries},[233]),[])

    def gate(self, required, decision='NOT_SCORED'):
        record={'status':'scored','feature_refusals':[{'step':232,'reason':'union size 129 exceeds 128'}]}
        outcome=types.SimpleNamespace(decision=decision,joint={'reason':'eligible_step_unscored',
            'detail':'missing score is not clean','eligible_unscored_positions':[233]})
        namespace={'json':json,'time':time,'uuid':uuid,'DetectorNoVerdict':NoVerdict,
            'DetectorEvidenceIncomplete':NoVerdict,'DetectorNotReady':NoVerdict,
            'enforcement_enabled':lambda:True,'detector_ready':lambda:True,'required_timeout':lambda:20,
            '_RUNTIME':types.SimpleNamespace(score=Mock(return_value=record)),
            '_join':lambda *a,**k:outcome,'_joint':types.SimpleNamespace(NOT_SCORED='NOT_SCORED',
                REASON_ELIGIBLE_UNSCORED='eligible_step_unscored'),
            'incomplete_feature_details':self.helper()}
        evaluate=extracted('detector_gate.py','evaluate',namespace)
        return lambda:evaluate(activation=types.SimpleNamespace(attaches=True,required=required),
            staged=({},{}),proof={},bound={},verdict={}), outcome

    def test_required_gap_remains_no_verdict_with_the_real_reason(self):
        run,_=self.gate(True)
        with self.assertRaisesRegex(NoVerdict,'union size 129 exceeds 128') as error:run()
        self.assertIn('233',str(error.exception))

    def test_advisory_gap_still_returns_no_detector_result(self):
        run,_=self.gate(False)
        self.assertIsNone(run())

    def test_completed_outcome_is_unchanged(self):
        for decision in ('NO_VIOLATION','VIOLATION','AMBER'):
            run,outcome=self.gate(True,decision)
            self.assertIs(run(),outcome)
