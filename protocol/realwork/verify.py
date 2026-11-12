"""Research receiver for native node-emitted envelopes on container loopback.

Actual Quick and Full run on the unmodified native envelope. Prompt calibration
remains unapproved/shadow: this is expressly NOT the guarded production service.
Only computed verdicts are returned; exceptions and incomplete evidence abstain.

Requests are taken best first, not in arrival order (see Pending): a reader
thread keeps the socket drained while a replay runs, so the newest candidate
never waits behind re-sent requests for heights the chain has already passed.
"""
import argparse
from collections import OrderedDict, deque
import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
import time


def replay_seed(secret, envelope_sha256):
    """The replay seed for one envelope: the same envelope always gets the same
    draws on this verifier (also after a restart), and a prover, who does not
    hold the verifier's key, cannot predict them to grind a proof."""
    digest = hmac.new(secret, b'noet-replay-seed/v1:' + bytes.fromhex(envelope_sha256), hashlib.sha256).digest()
    return int.from_bytes(digest[:4], 'little')


def native_height(height):
    """A block height the node may ask about: any positive 32-bit height. (The
    research bound of 1,000 stopped the testnet at height 1,001.)"""
    return type(height) is int and 1 <= height < 2 ** 31


class Pending:
    """The requests waiting for this verifier, best first.

    The node re-sends every unanswered request with a doubling delay, and keeps
    re-sending Full requests for competing blocks at heights the chain has
    passed (a block without its Full verdict is never stale to the node). In
    arrival order the one request the chain is waiting on, a candidate one
    above the tip, queued behind hours of those. Taken here, in order:

      1. heights above the highest block this verifier has answered GREEN
         (a candidate at or below it can no longer extend the chain), before
         heights at or below it;
      2. the highest height first;
      3. Quick before Full at the same height (about 20 s against 220 s);
      4. arrival order.

    Nothing gets a different verdict: the order only decides what is replayed
    next. A request already waiting is not queued twice (the node's re-send is
    byte-identical, and one answer serves both). Over the limit the worst
    request is let go unanswered, which the node treats as no answer yet: it
    sends it again later. A higher height can only come from a block whose
    parent is indexed, so pushing a request ahead costs a mined block each time
    and can delay, never stop, the candidate behind it.
    """

    def __init__(self, limit=256):
        self.limit = limit
        self.items = {}
        self.seq = 0
        self.green = 0

    def __len__(self):
        return len(self.items)

    def rank(self, entry):
        height = entry['height'] if type(entry['height']) is int else 0
        return (height <= self.green, -height, entry['kind'] == 2, entry['seq'])

    def add(self, sha, raw, height, kind, now):
        """Queue one request. Returns 'duplicate', or the entries let go (the
        new one included, when it ranks worst)."""
        if sha in self.items:
            return 'duplicate'
        self.items[sha] = {'sha': sha, 'raw': raw, 'height': height, 'kind': kind, 'seq': self.seq, 'arrived': now}
        self.seq += 1
        dropped = []
        while len(self.items) > self.limit:
            worst = max(self.items.values(), key=self.rank)
            dropped.append(self.items.pop(worst['sha']))
        return dropped

    def pop(self):
        if not self.items:
            return None
        return self.items.pop(min(self.items.values(), key=self.rank)['sha'])

    def answered(self, height, kind, verdict):
        if kind == 2 and verdict == 'GREEN' and type(height) is int:
            self.green = max(self.green, height)


def request_fields(raw, unpack, block_fields):
    """(height, validation kind) of a native request, None for a part that does
    not parse: such a request still runs, ranked as height 0, and its exception
    is recorded as before."""
    try:
        height, _ = block_fields(raw)
    except Exception:
        height = None
    try:
        kind = unpack(raw)['validation_type']
    except Exception:
        kind = None
    return height, kind


def run(output, deadline_seconds, capture_detector=False):
    import flatbuffers
    import numpy as np
    import pfunpack
    import random
    import torch
    import zmq
    import detector_gate
    from enhanced_logger import create_logger
    from proof_verifier import ProofVerifier
    from noet.verifier_entry import require_challenge_binding
    from noet.verifier_transport import check_request
    from utils.proof import ValidationResponse
    from utils.request_context import read_block_validation_fields
    from zmq_send_broker import build_envelope

    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    require_challenge_binding()
    # Keep the limitation explicit. Do not claim reviewed calibration by setting
    # an allowlist to get past the guarded production entry point.
    if os.environ.get('NOET_REQUIRE_PROMPT_CALIBRATION') == '1':
        raise ValueError('This research receiver cannot replace the guarded service')
    os.environ['POW_PROMPT_GIBBERISH_GATE']='shadow'
    secret=bytes.fromhex(os.environ['VALIDATOR_SHARED_SECRET'])
    if len(secret)!=32: raise ValueError('Expected 32-byte test transport key')
    torch.set_num_threads(3); torch.set_num_interop_threads(1)
    logger=create_logger(name='noet-native-research',reports_dir=str(output/'reports'))
    detector_gate.warm_on_start(logger)
    if not detector_gate.detector_ready(): raise RuntimeError('Sealed detector unavailable')
    context=zmq.Context()
    pull=context.socket(zmq.PULL); push=context.socket(zmq.PUSH)
    for sock in (pull,push):
        sock.setsockopt(zmq.LINGER,0);sock.setsockopt(zmq.MAXMSGSIZE,32*1024*1024)
        sock.setsockopt(zmq.RCVHWM,4);sock.setsockopt(zmq.SNDHWM,4)
    request_port=int(os.environ.get('VALIDATOR_PUSH_PORT','18555'))
    response_port=int(os.environ.get('VALIDATOR_PULL_PORT','18556'))
    if not 1024<=request_port<=65535 or not 1024<=response_port<=65535 or request_port==response_port:
        raise ValueError('Expected distinct unprivileged loopback verifier ports')
    pull.bind('tcp://127.0.0.1:'+str(request_port))
    push.connect('tcp://127.0.0.1:'+str(response_port))
    (output/'ready.json').write_text(json.dumps({'ready':True,'productionReady':False})+'\n')
    deadline=time.monotonic()+deadline_seconds
    count=0
    completed=OrderedDict()
    pending=Pending()
    inbox=deque();arrived=threading.Event();stop=threading.Event();failed=[]

    def receive():
        # Owns the PULL socket: drains it while a replay runs on the main thread.
        try:
            while not stop.is_set():
                if pull.poll(500):
                    inbox.append((time.monotonic(),pull.recv_multipart()));arrived.set()
        except Exception as error:
            failed.append(error);arrived.set()

    def note(**event):
        event['at']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
        with (output/'queue.jsonl').open('a') as stream:stream.write(json.dumps(event)+'\n')

    def intake():
        """Move what the reader received into the queue; answer exact repeats
        of a completed Full at once, as before."""
        while inbox:
            at,frames=inbox.popleft()
            if len(frames)!=1:raise ValueError('Expected one native request frame')
            raw=frames[0];check_request(raw)
            sha=hashlib.sha256(raw).hexdigest()
            if sha in completed:
                handle(raw,{'queuedSeconds':time.monotonic()-at,'queueDepth':len(pending)});continue
            height,kind=request_fields(raw,pfunpack.unpack_validation_request,read_block_validation_fields)
            dropped=pending.add(sha,raw,height,kind,at)
            if dropped!='duplicate':
                for entry in dropped:
                    note(event='dropped',envelopeSha256=entry['sha'],height=entry['height'],kind=entry['kind'],
                         waitedSeconds=round(time.monotonic()-entry['arrived'],1),green=pending.green)

    def handle(raw,queued):
            nonlocal count
            item=output/str(count);item.mkdir();count+=1
            (item/'request.bin').write_bytes(raw)
            result={'envelopeSha256':hashlib.sha256(raw).hexdigest(),'nodeEmittedEnvelope':True,
                    'productionReady':False,'promptCalibrationApproved':False,**queued}
            started=time.monotonic()
            try:
                request=pfunpack.unpack_validation_request(raw)
                height,ruleset=read_block_validation_fields(raw)
                result.update(height=height,ruleset=ruleset,requestType=request['validation_type'])
                if request['request_type']!=1 or not native_height(height):
                    raise ValueError('Unexpected native validation request')
                cache_key=result['envelopeSha256']
                if cache_key in completed:
                    payload,envelope,prior=completed[cache_key]
                    push.send_multipart([payload,envelope])
                    result.update(height=height,ruleset=ruleset,responseSent=True,
                        responseCode=prior['responseCode'],fullVerdict=prior.get('fullVerdict'),
                        reusedExactEnvelope=True,computedResultDirectory=prior['computedResultDirectory'],
                        elapsedSeconds=time.monotonic()-started)
                    (item/'result.json').write_text(json.dumps(result,indent=2)+'\n')
                    return result
                body=request['request'];proof=body['pow_blob']
                result.update(difficulty=body['difficulty'],model=proof['model_identifier'])
                seed=replay_seed(secret,cache_key);result['replaySeed']=seed
                random.seed(seed);np.random.seed(seed);torch.manual_seed(seed)
                verifier=ProofVerifier(logger=logger)
                kind=request['validation_type']
                if kind==2:
                    runtime=detector_gate.runtime()
                    score=runtime.score
                    def observed_score(snapshot, **kwargs):
                        result['replayViews']=[str(name) for name in snapshot['arrays'].get('cand_names',[])]
                        if capture_detector:
                            try:
                                from verify_current import capture_detector_inputs
                                result['detectorCapture']=capture_detector_inputs(snapshot,item/'detector.npz')
                            except Exception as error:
                                result['detectorCaptureError']=type(error).__name__+': '+str(error)[:300]
                        record=score(snapshot,**kwargs)
                        result['detectorRecordStatus']=record.get('status')
                        result['detectorSteps']=record.get('steps')
                        result['detectorFeatureRefusals']=record.get('feature_refusals',[])
                        return record
                    runtime.score=observed_score
                    try:verdict=verifier.full_verify(raw)
                    finally:runtime.score=score
                    result['fullVerdict']=verdict
                    code={'GREEN':6,'AMBER':7,'RED':8}[verdict]
                elif kind in (0,1):
                    code=int(verifier.quick_verify(raw) if kind==0 else verifier.quick_verify_smell_test(raw))
                    result['quickVerdict']=code
                else:raise ValueError('Unsupported native request kind')
                gate=getattr(verifier,'_last_full_gate',None) or {}
                if gate:result['fullGate']={k:gate.get(k) for k in ('status','max_logit_severity','max_abs_prob_residual')}
                result['promptScore']=getattr(verifier,'_last_prompt_gibberish_decision',None)
                result['detector']=getattr(verifier,'_poi_detector_result',None)
                if verifier.model is not None:
                    result['modelDevices']=sorted({str(p.device) for p in verifier.model.parameters()})
                    result['modelDtypes']=sorted({str(p.dtype) for p in verifier.model.parameters()})
                if torch.cuda.is_available():result['peakCudaBytes']=torch.cuda.max_memory_allocated()
                builder=flatbuffers.Builder(256)
                request_id=bytes(request['hash_id'])
                vec=builder.CreateByteVector(request_id)
                ValidationResponse.Start(builder)
                ValidationResponse.AddHashIdentifier(builder,vec)
                ValidationResponse.AddEnumResponse(builder,code)
                builder.Finish(ValidationResponse.End(builder))
                payload=bytes(builder.Output())
                envelope=build_envelope(secret,request_id,code,ruleset,payload)
                push.send_multipart([payload,envelope])
                result['responseCode']=code
                result['responseSent']=True
                # Native retries must not queue repeated model inference. Cache
                # completed GREEN/RED Full verdicts and Quick verdicts (the node
                # re-sends a Quick every few seconds while it runs), keyed by
                # every envelope byte: the same bytes get the same replay seed,
                # and changed proof/context/ruleset never reuses a prior decision.
                if (kind==2 and code in (6,8)) or kind in (0,1):
                    completed[cache_key]=(payload,envelope,{'responseCode':code,
                        'fullVerdict':result.get('fullVerdict'),'computedResultDirectory':item.name})
                    while len(completed)>64:completed.popitem(last=False)
                del verifier
            except Exception as error:
                result.update(exceptionType=type(error).__name__,exception=str(error)[:1500],responseSent=False)
            result['elapsedSeconds']=time.monotonic()-started
            (item/'result.json').write_text(json.dumps(result,indent=2,default=str)+'\n')
            return result

    reader=threading.Thread(target=receive,name='verifier-intake',daemon=True)
    reader.start()
    try:
        while time.monotonic()<deadline:
            if failed:raise failed[0]
            intake()
            entry=pending.pop()
            if entry is None:
                arrived.wait(1.0);arrived.clear();continue
            result=handle(entry['raw'],{'queuedSeconds':time.monotonic()-entry['arrived'],'queueDepth':len(pending),
                                        'queueGreenHeight':pending.green})
            pending.answered(result.get('height'),result.get('requestType'),result.get('fullVerdict'))
    finally:
        stop.set();reader.join(5)
        detector_gate.runtime().close()
        pull.close();push.close();context.term()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--seconds',type=int,default=1800)
    p.add_argument('--capture-detector',action='store_true')
    a=p.parse_args()
    if not 1<=a.seconds<=86400:p.error('Bound the research receiver to one day')
    run(a.out,a.seconds,a.capture_detector)
