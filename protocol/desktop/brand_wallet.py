#!/usr/bin/env python3
"""Apply presentation-only NOET branding to the pinned native Qt wallet."""
from pathlib import Path
import sys

root = Path(sys.argv[1]) / "src/qt"
for name in ("guiconstants.h", "bitcoinunits.cpp", "networkstyle.cpp", "splashscreen.cpp", "bitcoingui.cpp"):
    path = root / name
    text = path.read_text()
    # Preserve author/license notices and C++ symbol names. Only quoted display strings.
    import re
    text = re.sub(r'"(?:[^"\\]|\\.)*"', lambda m: m[0].replace("TensorCash", "NOET").replace("tensorcash.org", "noet.network").replace("mTSC", "mNOET").replace("µTSC", "µNOET").replace('"TSC"', '"NOET"'), text)
    if name == "networkstyle.cpp":
        text = text.replace("[NOET lab — test coins]", "[NOET lab | test coins]")
    if name == "networkstyle.cpp" and "[NOET lab | test coins]" not in text:
        text = text.replace('QAPP_APP_NAME_TENSOR_REG, 200, 30', 'QAPP_APP_NAME_TENSOR_REG, 0, 0')
        text = text.replace('    for (const auto& network_style', '    if (networkId == ChainType::TENSOR_REG) titleAddText = "[NOET lab | test coins]";\n    for (const auto& network_style')
    path.write_text(text)

# Display labels only. Preserve the internal TSC filter value and transaction data.
for name, replacements in {
    "sendcoinsentry.cpp": {
        "TSC (TensorCash)": "NOET",
        "Select the asset to send (TSC or other assets)": "Select the asset to send (NOET or other assets)",
        "Fees are paid in TSC.": "Fees are paid in NOET.",
    },
    "receivecoinsdialog.cpp": {
        'addItem("TSC", QVariant())': 'addItem("NOET", QVariant())',
        'addItem("TSC", QStringLiteral("TSC"))': 'addItem("NOET", QStringLiteral("TSC"))',
    },
    "test/sendcoinsentrytests.cpp": {
        'QString("BTC (Bitcoin)")': 'QString("NOET")',
    },
}.items():
    path = root / name
    text = path.read_text()
    for old, new in replacements.items():
        text = text.replace(old, new)
    path.write_text(text)
