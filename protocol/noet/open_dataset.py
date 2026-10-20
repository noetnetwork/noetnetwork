"""The open dataset miners work through when no one is waiting for an answer.

NOET open explanations, version 1: plain-language explanations of common ideas
in science, mathematics, computing, health, money and civic life, written by
the network's model and published (CC0) with the prompt that produced each one.
Every pass over the list uses the next style, so each idea collects several
independent explanations. Edit `CONCEPTS` or `STYLES` only together with a new
`NAME`, so published rows keep the meaning of their source label.
"""

NAME = 'open-explanations-v1'

STYLES = (
    'Explain {concept} in plain language for a curious 15-year-old. Use one concrete everyday example '
    'and keep it under 250 words.',
    'Explain {concept} to an adult with no background in the subject. Start with a one-sentence summary, '
    'then give a short example and one common misconception.',
    'Write a short, accurate glossary entry for {concept}: a definition, why it matters, and a simple example.',
)

CONCEPTS = (
    # Physics and chemistry
    'gravity', 'friction', 'inertia', 'momentum', 'energy conservation', 'kinetic and potential energy',
    'electric current', 'voltage', 'electrical resistance', 'magnetism', 'electromagnetic waves', 'sound waves',
    'the Doppler effect', 'refraction of light', 'why the sky is blue', 'temperature and heat', 'entropy',
    'pressure', 'buoyancy', 'density', 'atoms', 'electrons, protons and neutrons', 'isotopes', 'radioactivity',
    'half-life', 'nuclear fission', 'nuclear fusion', 'chemical bonds', 'acids and bases', 'pH', 'oxidation',
    'catalysts', 'states of matter', 'evaporation', 'the periodic table', 'semiconductors', 'superconductivity',
    'the speed of light', 'special relativity', 'quantum superposition', 'black holes', 'the Big Bang',
    # Earth and life sciences
    'plate tectonics', 'earthquakes', 'volcanoes', 'the water cycle', 'the carbon cycle', 'the greenhouse effect',
    'climate versus weather', 'ocean currents', 'the seasons', 'the phases of the Moon', 'tides', 'erosion',
    'photosynthesis', 'cellular respiration', 'DNA', 'genes', 'proteins', 'enzymes', 'cells', 'mitosis',
    'evolution by natural selection', 'mutations', 'ecosystems', 'food chains', 'biodiversity', 'pollination',
    'bacteria', 'viruses', 'fungi', 'the microbiome', 'symbiosis', 'invasive species',
    # Health and the body
    'the immune system', 'vaccines', 'antibiotics', 'antibiotic resistance', 'herd immunity', 'blood pressure',
    'cholesterol', 'blood sugar and insulin', 'the heart', 'the lungs', 'the kidneys', 'the nervous system',
    'sleep', 'hydration', 'calories', 'protein in the diet', 'fiber', 'vitamins', 'exercise and muscles',
    'stress hormones', 'placebo effect', 'clinical trials', 'allergies', 'inflammation', 'first aid for burns',
    'hand washing', 'sun protection',
    # Mathematics and statistics
    'prime numbers', 'fractions', 'percentages', 'ratios and proportions', 'negative numbers', 'exponents',
    'logarithms', 'square roots', 'pi', 'the Pythagorean theorem', 'area and perimeter', 'volume',
    'functions', 'slope of a line', 'linear equations', 'quadratic equations', 'derivatives', 'integrals',
    'limits', 'infinity', 'mathematical proof', 'sets', 'probability', 'independent events', 'expected value',
    'the law of large numbers', 'mean, median and mode', 'standard deviation', 'the normal distribution',
    'correlation versus causation', 'sampling bias', 'statistical significance', 'confidence intervals',
    'compound interest (the mathematics)', 'exponential growth', 'vectors', 'matrices', 'graphs in graph theory',
    'binary numbers', 'modular arithmetic', 'rounding error', 'significant figures',
    # Computing
    'algorithms', 'data structures', 'arrays and lists', 'hash tables', 'sorting', 'binary search', 'recursion',
    'Big-O notation', 'compilers', 'operating systems', 'memory and storage', 'the CPU', 'the GPU',
    'how the internet routes data', 'IP addresses', 'DNS', 'HTTP and HTTPS', 'encryption', 'public-key cryptography',
    'hash functions', 'digital signatures', 'passwords and password managers', 'two-factor authentication',
    'phishing', 'malware', 'backups', 'open-source software', 'version control', 'databases', 'SQL', 'APIs',
    'cloud computing', 'virtual machines', 'containers', 'machine learning', 'neural networks',
    'training versus inference', 'large language models', 'tokens in language models', 'overfitting',
    'bias in AI systems', 'floating-point numbers', 'random number generators', 'compression', 'error correction',
    # Money and economics
    'inflation', 'interest rates', 'compound interest', 'budgeting', 'emergency savings', 'credit scores',
    'loans and mortgages', 'stocks', 'bonds', 'index funds', 'diversification', 'risk and return', 'taxes',
    'supply and demand', 'opportunity cost', 'comparative advantage', 'gross domestic product', 'unemployment',
    'central banks', 'exchange rates', 'monopolies', 'externalities', 'public goods', 'scams and fraud warning signs',
    'how a blockchain works', 'proof of work', 'digital wallets and private keys',
    # Society, language and reasoning
    'democracy', 'separation of powers', 'the rule of law', 'human rights', 'elections and voting systems',
    'census', 'taxes and public services', 'contracts', 'copyright', 'patents', 'privacy', 'free speech',
    'the scientific method', 'peer review', 'hypotheses and theories', 'control groups', 'logical fallacies',
    'confirmation bias', 'critical thinking', 'evaluating sources', 'misinformation', 'metaphor', 'grammar',
    'translation', 'dialects and languages', 'writing systems', 'maps and map projections', 'time zones',
    'calendars', 'units of measurement', 'recycling', 'renewable energy', 'batteries', 'electric cars',
    'nuclear power', 'water treatment', 'sanitation', 'agriculture and crop rotation', 'soil', 'urban planning',
    'public transport', 'bridges and how they carry loads', 'the printing press', 'the industrial revolution',
)


def item(position):
    """(source label, prompt) for one position in the never-ending pass over the list."""
    if not isinstance(position, int) or position < 0:
        raise ValueError('Dataset position must be a non-negative integer')
    rounds, index = divmod(position, len(CONCEPTS))
    style = STYLES[rounds % len(STYLES)]
    return f'{NAME}:{index}:{rounds}', style.format(concept=CONCEPTS[index])
