"""Headless Linux GUI acceptance. Never connects to a node or real wallet."""
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch
from noet.native_host import desktop_approval, desktop_send_approval

class ApprovalWindowTests(unittest.TestCase):
    def exercise(self,action,send=False):
        real_tk=tk.Tk
        errors=[]
        def create():
            root=real_tk()
            def fail(*error):
                errors.append(error);root.destroy()
            root.report_callback_exception=fail
            def inspect_and_act():
                widgets=[]
                def descend(parent):
                    for child in parent.winfo_children():
                        widgets.append(child);descend(child)
                descend(root)
                text=next(item for item in widgets if isinstance(item,tk.Text))
                self.assertEqual(str(text.cget('state')),'disabled')
                self.assertIn('nlab1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq50ehkw',text.get('1.0','end'))
                self.assertIn('External recipient',text.get('1.0','end'))
                self.assertIn('ab'*32,text.get('1.0','end'))
                labels=[str(item.cget('text')) for item in widgets if isinstance(item,ttk.Label)]
                self.assertIn('Network fee',labels)
                self.assertIn('0.00000140 test NOET',labels)
                entry=next(item for item in widgets if isinstance(item,ttk.Entry))
                self.assertTrue(entry.cget('show'))
                buttons={str(item.cget('text')):item for item in widgets if isinstance(item,ttk.Button)}
                approval = 'Sign and send test coins' if send else 'Sign transaction'
                if send:
                    self.assertIn('Sending cannot be undone.',text.get('1.0','end'))
                    self.assertNotIn('It does not send the transaction',text.get('1.0','end'))
                confirm=next(item for item in widgets if isinstance(item,ttk.Checkbutton))
                self.assertTrue(buttons[approval].instate(['disabled']))
                if action=='approve':
                    confirm.invoke()
                    self.assertTrue(buttons[approval].instate(['disabled']))
                    confirm.invoke()
                    entry.insert(0,'disposable-test-passphrase')
                    self.assertTrue(buttons[approval].instate(['disabled']))
                    buttons[approval].invoke()
                    self.assertTrue(root.winfo_exists())
                    confirm.invoke()
                    self.assertFalse(buttons[approval].instate(['disabled']))
                    buttons[approval].invoke()
                elif action=='cancel':
                    entry.insert(0,'discard-on-cancel')
                    buttons['Cancel'].invoke()
                else:
                    entry.insert(0,'discard-on-close')
                    root.tk.call(root.protocol('WM_DELETE_WINDOW'))
            root.after(100,inspect_and_act)
            root.after(5000,lambda:fail(RuntimeError,'Approval test timed out',None))
            return root
        with patch.object(tk,'Tk',create):
            result=(desktop_send_approval if send else desktop_approval)({'network':'NOET laboratory; test coins only','broadcast':False,'psbtFingerprint':'ab'*32,'inputs':1,'estimatedVsize':140,'locktime':0,'replaceable':True,'feeTestNOET':'0.00000140','outputs':[{'address':'nlab1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq50ehkw','testNOET':'1.23456789','ownership':'External recipient'}]})
        self.assertEqual(errors,[])
        return result
    def test_explicit_approval(self):self.assertEqual(self.exercise('approve'),'disposable-test-passphrase')
    def test_explicit_send_approval(self):self.assertEqual(self.exercise('approve',True),'disposable-test-passphrase')
    def test_send_cancel(self):self.assertIsNone(self.exercise('cancel',True))
    def test_send_close(self):self.assertIsNone(self.exercise('close',True))
    def test_cancel(self):self.assertIsNone(self.exercise('cancel'))
    def test_close_window(self):self.assertIsNone(self.exercise('close'))

if __name__=='__main__':unittest.main(verbosity=2)
