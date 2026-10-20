import copy
import unittest
from cpu.verify_current import replay_passed

BASE = {'quickVerdict':0,'fullVerdict':'GREEN','modelLoaded':True,
        'replayViews':['1','2','5','10','20','spda'],'detectorRecordStatus':'scored',
        'detectorSupport':{'six_view_cloud_present':{'status':'evaluated'}}}


class ReplayProfileTests(unittest.TestCase):
    def test_required_profile_cannot_pass_with_advisory_or_missing_detector(self):
        self.assertTrue(replay_passed(BASE | {'ruleset':4,'detector':{'detector_status':'required'}}))
        for detector in ({}, {'detector_status':'advisory'}):
            self.assertFalse(replay_passed(BASE | {'ruleset':4,'detector':detector}))

    def test_recorded_early_profile_is_a_diagnostic_not_required_acceptance(self):
        result=BASE | {'ruleset':2,'nodeIssuedHeader':True,'nodeEmittedValidationRequest':False,
                       'nodeWorkRecordSha256':'a'*64}
        self.assertTrue(replay_passed(result))
        self.assertFalse(replay_passed(result | {'ruleset':4}))
        for key in ('nodeIssuedHeader','nodeEmittedValidationRequest','nodeWorkRecordSha256'):
            invalid=copy.deepcopy(result);invalid.pop(key)
            self.assertFalse(replay_passed(invalid))

    def test_no_profile_accepts_unscored_or_partial_replay(self):
        for changes in ({'replayViews':['1','2','spda']},{'detectorRecordStatus':'unsupported'},
                        {'modelLoaded':False},{'exception':'allocation failed'},
                        {'fullVerdict':'RED'},{'fullVerdict':'AMBER'},{'quickVerdict':1}):
            result=BASE | {'ruleset':4,'detector':{'detector_status':'required'}} | changes
            self.assertFalse(replay_passed(result))

    def test_unknown_profile_does_not_inherit_acceptance(self):
        for ruleset in (None,0,1,3,5):
            self.assertFalse(replay_passed(BASE | {'ruleset':ruleset,'detector':{'detector_status':'required'}}))
