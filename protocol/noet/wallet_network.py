"""Pinned development identities. No arbitrary chain or mainnet signing."""
PROFILES = {
    'lab': {'genesis': 'fc0ac105e8adb2401780e6fd54ec48650912c5e460d0a4ffe14ba1b83e2df359', 'prefix': 'nlab', 'name': 'NOET laboratory'},
    'private-work': {'genesis': '301587d540aafc312cf4606e700d9662e038e7e43ca6cca92779d6b4baa81b4e', 'prefix': 'nwrk', 'name': 'NOET private work'},
}

def profile_for(genesis):
    for profile in PROFILES.values():
        if profile['genesis'] == genesis:
            return profile
    raise ValueError('Unrecognized wallet network')
