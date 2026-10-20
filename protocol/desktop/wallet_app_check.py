"""Headless Tk controller acceptance with synthetic responses; no keys or network."""
import importlib.util
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('desktop',ROOT/'wallets/native/desktop.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
from noet.wallet_network import PROFILES
from noet.wallet_prompt import EXTERNAL
PROFILE=PROFILES['lab'];ADDRESS='nlab1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq50ehkw'
REVIEW={'network':'NOET laboratory; test coins only','networkGenesis':PROFILE['genesis'],'networkName':PROFILE['name'],
    'broadcast':False,'psbtFingerprint':'ab'*32,'inputs':1,'estimatedVsize':140,'locktime':0,'replaceable':True,
    'feeTestNOET':'0.00000280','outputs':[{'address':ADDRESS,'testNOET':'1.00000000','ownership':EXTERNAL}]}

class DesktopWindowTests(unittest.TestCase):
    def exercise(self,mode):
        calls=[];errors=[];real_tk=tk.Tk
        def exchange(command,request):
            calls.append(request)
            method=request['method']
            if method=='status':return {'genesis':PROFILE['genesis'],'addressPrefix':PROFILE['prefix'],'testCoinsOnly':True}
            if method=='balances':return {'genesis':PROFILE['genesis'],'testCoinsOnly':True,'trusted':'12.12345678','pending':'0.00000000','immature':'9.00000000'}
            if method=='backup':return {'saved':True,'encrypted':True,'genesis':PROFILE['genesis']} if mode=='backup' else {'cancelled':True,'saved':False}
            if method=='receive':return {'address':ADDRESS}
            if method=='prepare':return {'psbt':'cHNidP8A','review':REVIEW,'broadcast':False}
            if method=='send':
                if mode=='cancel':return {'cancelled':True,'broadcast':False}
                if mode=='uncertain':raise RuntimeError('Lost response')
                return {'broadcast':True,'genesis':PROFILE['genesis'],'txid':'cd'*32}
            if method=='history':return {'genesis':PROFILE['genesis'],'transactions':[{'category':'send','amount':'-1.00000000','confirmations':2,'txid':'cd'*32}]}
            raise AssertionError(method)
        def create():
            root=real_tk();stage=[0]
            def fail(error):errors.append(error);root.destroy()
            root.report_callback_exception=lambda kind,value,tb:fail(value)
            def inspect():
                try:
                    widgets=[]
                    def walk(parent):
                        for child in parent.winfo_children():widgets.append(child);walk(child)
                    walk(root)
                    buttons={str(w.cget('text')):w for w in widgets if isinstance(w,ttk.Button)}
                    entries=[w for w in widgets if isinstance(w,ttk.Entry)]
                    texts=[w for w in widgets if isinstance(w,tk.Text)]
                    labels='\n'.join(str(w.cget('text')) for w in widgets if isinstance(w,ttk.Label))
                    for item in texts:self.assertEqual(str(item.cget('state')),'disabled')
                    for item in entries:self.assertFalse(item.cget('show')) # no password field in the app
                    if stage[0]==0:
                        self.assertTrue(buttons['Sign and send'].instate(['disabled']))
                        buttons['Connect / refresh'].invoke();stage[0]=1
                    elif stage[0]==1 and buttons['New address'].instate(['!disabled']):
                        self.assertIn('12.12345678',labels)
                        if mode.startswith('backup'):
                            buttons['Encrypted backup'].invoke();stage[0]=6
                        else:buttons['New address'].invoke();stage[0]=2
                    elif stage[0]==2 and buttons['New address'].instate(['!disabled']):
                        self.assertEqual(entries[0].get(),ADDRESS)
                        entries[1].insert(0,ADDRESS);entries[2].insert(0,'1')
                        buttons['Review transfer'].invoke();stage[0]=3
                    elif stage[0]==3 and buttons['Sign and send'].instate(['!disabled']):
                        self.assertIn(PROFILE['genesis'],texts[0].get('1.0','end'))
                        self.assertIn('1.00000000',texts[0].get('1.0','end'))
                        if mode=='edit':
                            entries[2].delete(0,'end');entries[2].insert(0,'2')
                            self.assertTrue(buttons['Sign and send'].instate(['disabled']))
                            root.destroy();return
                        buttons['Sign and send'].invoke();stage[0]=4
                    elif stage[0]==4 and buttons['Connect / refresh'].instate(['!disabled']):
                        self.assertTrue(buttons['Sign and send'].instate(['disabled']))
                        if mode=='cancel':self.assertIn('Cancelled. Nothing sent.',labels)
                        elif mode=='uncertain':
                            self.assertIn('check Activity before trying again',labels)
                            self.assertIn('Balances unavailable',labels)
                            self.assertTrue(buttons['New address'].instate(['disabled']))
                        else:
                            self.assertIn('cd'*32,texts[0].get('1.0','end'))
                            buttons['Refresh activity'].invoke();stage[0]=5;root.after(10,inspect);return
                        root.destroy();return
                    elif stage[0]==5 and buttons['Refresh activity'].instate(['!disabled']):
                        self.assertIn('-1.00000000 test NOET',texts[1].get('1.0','end'))
                        self.assertIn('2 confirmations',texts[1].get('1.0','end'))
                        root.destroy();return
                    elif stage[0]==6 and buttons['Encrypted backup'].instate(['!disabled']):
                        self.assertIn('Encrypted backup saved.' if mode=='backup' else 'Backup cancelled.',labels)
                        root.destroy();return
                    root.after(10,inspect)
                except Exception as error:fail(error)
            root.after(30,inspect)
            root.after(7000,lambda:fail(TimeoutError('Desktop acceptance timed out')))
            return root
        with patch.object(tk,'Tk',create),patch.object(app,'connection',return_value=['unused']),patch.object(app,'exchange',side_effect=exchange):
            app.launch('/unused')
        if errors:raise errors[0]
        self.assertNotIn('password',str(calls))
        self.assertEqual(sum(row['method']=='send' for row in calls),0 if mode=='edit' or mode.startswith('backup') else 1)
    def test_receive_review_edit_invalidates_draft(self):self.exercise('edit')
    def test_encrypted_backup(self):self.exercise('backup')
    def test_cancel_backup(self):self.exercise('backup_cancel')
    def test_send_cancellation(self):self.exercise('cancel')
    def test_send_and_history(self):self.exercise('send')
    def test_uncertain_send_requires_reconnection(self):self.exercise('uncertain')

if __name__=='__main__':unittest.main(verbosity=2)
