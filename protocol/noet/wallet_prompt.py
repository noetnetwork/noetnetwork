"""Readable native approval for the restricted laboratory signing wallet."""
import re
from .wallet_review import amount, coin
from .wallet_network import PROFILES, profile_for

OWNED = 'This wallet (verify intended change)'
EXTERNAL = 'External recipient'


def display_review(review):
    """Render reviewed amounts exactly; never round, hide or abbreviate outputs."""
    if not isinstance(review, dict) or review.get('broadcast') is not False or review.get('network') != 'NOET laboratory; test coins only':
        raise ValueError('Unsupported approval review')
    outputs = review.get('outputs')
    if not isinstance(outputs, list) or not 1 <= len(outputs) <= 100:
        raise ValueError('Invalid approval outputs')
    fingerprint = review.get('psbtFingerprint')
    if not isinstance(fingerprint, str) or not re.fullmatch('[a-f0-9]{64}', fingerprint):
        raise ValueError('Invalid transaction fingerprint')
    for field in ('inputs', 'estimatedVsize', 'locktime'):
        if type(review.get(field)) is not int or review[field] < (0 if field == 'locktime' else 1):
            raise ValueError('Invalid transaction details')
    if type(review.get('replaceable')) is not bool:
        raise ValueError('Invalid transaction replacement flag')
    def exact(value):
        if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9][0-9]*)\.[0-9]{8}', value):
            raise ValueError('Approval amounts must have exact precision')
        return amount(value)
    fee = exact(review.get('feeTestNOET'))
    profile = profile_for(review.get('networkGenesis', PROFILES['lab']['genesis']))
    if 'networkName' in review and review['networkName'] != profile['name']:
        raise ValueError('Approval network identity mismatch')
    external = own = 0
    details = [profile['name']+' · Test coins only', 'Genesis: '+profile['genesis'], '']
    for index, output in enumerate(outputs, 1):
        if not isinstance(output, dict) or output.get('ownership') not in (OWNED, EXTERNAL):
            raise ValueError('Unknown output ownership')
        address = output.get('address')
        if not isinstance(address, str) or not re.fullmatch('(?:nlab|nwrk)1[023456789acdefghjklmnpqrstuvwxyz]{6,85}', address):
            raise ValueError('Invalid address display')
        if not address.startswith(profile['prefix']+'1'):
            raise ValueError('Output belongs to a different approval network')
        value = exact(output.get('testNOET'))
        owned = output['ownership'] == OWNED
        if owned: own += value
        else: external += value
        label = 'Back to this wallet (check intended change)' if owned else 'External recipient'
        details += [f'{index}. {label}', address, coin(value)+' test NOET', '']
    details += ['TRANSACTION DETAILS', f'Inputs: {review["inputs"]}',
        f'Estimated size: {review["estimatedVsize"]} virtual bytes',
        'Fee replacement: '+('allowed' if review['replaceable'] else 'not signalled'),
        f'Locktime: {review["locktime"]}', '', 'TRANSACTION FINGERPRINT', fingerprint,
        '', 'Every output is listed above. Signing creates a signed transaction file.',
        'It does not send the transaction to the network.']
    return {'external':coin(external), 'own':coin(own), 'fee':coin(fee),
        'leaving':coin(external+fee), 'network':profile['name'], 'details':'\n'.join(details)}


def desktop_approval(review, *, send=False):
    """The passphrase and confirmation are collected only in the native window."""
    display = display_review(review)  # Reject incomplete reviews before opening a window.
    if send:
        display['details'] = display['details'].replace('Signing creates a signed transaction file.\nIt does not send the transaction to the network.', 'Approving signs and sends this payment to the connected development network.\nSending cannot be undone. Check every recipient and the fee.')
    import tkinter as tk
    from tkinter import ttk
    root = tk.Tk()
    root.title('NOET Wallet | Review transaction')
    root.geometry('760x780')
    root.minsize(600, 620)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(2, weight=1)
    result = {'password': None}
    password = tk.StringVar(root)
    confirmed = tk.BooleanVar(root, False)
    header = ttk.Frame(root, padding=(24, 20, 24, 12))
    header.grid(row=0, column=0, sticky='ew')
    ttk.Label(header, text='Send test coins' if send else 'Review transaction', font=('TkDefaultFont', 20, 'bold')).pack(anchor='w')
    ttk.Label(header, text=display['network']+' · Test coins only', padding=(0, 6, 0, 0)).pack(anchor='w')
    summary = ttk.Frame(root, padding=(24, 0, 24, 16))
    summary.grid(row=1, column=0, sticky='ew')
    summary.columnconfigure(1, weight=1)
    for row, (label, key) in enumerate((('To other wallets', 'external'), ('Back to this wallet', 'own'),
        ('Network fee', 'fee'), ('Total leaving this wallet', 'leaving'))):
        ttk.Label(summary, text=label).grid(row=row, column=0, sticky='w', pady=3)
        ttk.Label(summary, text=display[key]+' test NOET').grid(row=row, column=1, sticky='e', pady=3)
    frame = ttk.Frame(root, padding=(24, 0, 24, 0))
    frame.grid(row=2, column=0, sticky='nsew')
    frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
    details = tk.Text(frame, wrap='char', font='TkFixedFont', padx=12, pady=12,
        borderwidth=1, relief='solid', height=12)
    scroll = ttk.Scrollbar(frame, command=details.yview)
    details.configure(yscrollcommand=scroll.set)
    details.insert('1.0', display['details']); details.configure(state='disabled')
    details.grid(row=0, column=0, sticky='nsew'); scroll.grid(row=0, column=1, sticky='ns')
    footer = ttk.Frame(root, padding=(24, 14, 24, 20))
    footer.grid(row=3, column=0, sticky='ew')
    footer.columnconfigure(0, weight=1)
    ttk.Checkbutton(footer, text='I checked every address and the fee.', variable=confirmed).grid(row=0, column=0, sticky='w', pady=(0, 12))
    ttk.Label(footer, text='Wallet passphrase').grid(row=1, column=0, sticky='w')
    entry = ttk.Entry(footer, textvariable=password, show='•')
    entry.grid(row=2, column=0, sticky='ew', pady=(6, 6))
    ttk.Label(footer, text='This approval signs and broadcasts the payment.' if send else 'The passphrase stays in this native process.', wraplength=500, justify='left').grid(row=3, column=0, sticky='w')
    buttons = ttk.Frame(footer)
    buttons.grid(row=4, column=0, sticky='e', pady=(16, 0))
    def cancel():
        password.set('')
        root.destroy()
    def accept():
        if confirmed.get() and password.get():
            result['password'] = password.get()
            password.set('')
            root.destroy()
    cancel_button = ttk.Button(buttons, text='Cancel', command=cancel)
    cancel_button.pack(side='left', padx=(0, 12))
    approve = ttk.Button(buttons, text='Sign and send test coins' if send else 'Sign transaction', command=accept, state='disabled')
    approve.pack(side='left')
    def changed(*_):
        approve.configure(state='normal' if confirmed.get() and password.get() else 'disabled')
    traces = [(password, password.trace_add('write', changed)),
              (confirmed, confirmed.trace_add('write', changed))]
    root.protocol('WM_DELETE_WINDOW', cancel)
    root.bind('<Escape>', lambda _: cancel())
    cancel_button.focus_set()
    try:
        root.mainloop()
    finally:
        for variable, token in traces:
            try:
                variable.trace_remove('write', token)
            except tk.TclError:
                pass
        try:
            password.set('')
        except tk.TclError:
            pass
    return result['password']


def desktop_send_approval(review):
    return desktop_approval(review, send=True)
