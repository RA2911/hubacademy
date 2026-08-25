"""Learner Profiling (Phase 1) — measures learning CAPACITY and recommends a
best-fit LEARNING STRATEGY. Domain content lives here so it can be tested in
isolation from the web layer. Scoring is transparent and rule-based.

A learner answers a short hybrid task set (mostly multiple-choice + one open
"apply it" task graded by AI). Per task we capture: answer, difficulty, response
time, hint usage and self-rated confidence. From those signals we derive 0-100
capacity scores and map them to one of six learning-strategy archetypes.

Nothing here enrolls anyone or changes a course — the output is guidance the
learner is free to accept, adjust or ignore.
"""

# ---------------------------------------------------------------------------
# Task bank — general learning capacity (topic-independent so it works platform
# wide). Each item declares the capacity dimension it feeds and its difficulty
# (1 easy .. 5 hard). `type` is 'mcq' (auto-graded) or 'open' (AI-graded).
# ---------------------------------------------------------------------------

CAPACITY_TASKS = [
    {
        'id': 'c1', 'dimension': 'comprehension', 'level': 'Explain', 'difficulty': 1, 'type': 'mcq',
        'prompt': 'A "sunk cost" is money already spent that cannot be recovered. Which sentence best restates this idea?',
        'options': [
            'Money you have already spent and cannot get back, whatever you decide next.',
            'Money you expect to earn from a future project.',
            'The total budget you set aside before a project starts.',
            'A cost that is shared equally between two departments.',
        ],
        'answer': 0, 'hint': 'Focus on the words "already spent" and "cannot be recovered".',
    },
    {
        'id': 'c2', 'dimension': 'comprehension', 'level': 'Interpret', 'difficulty': 2, 'type': 'mcq',
        'prompt': 'A store\'s sales rose 5% while the whole market rose 15%. What does this most likely indicate?',
        'options': [
            'The store lost market share even though its sales grew.',
            'The store outperformed its competitors.',
            'The store\'s prices must have increased.',
            'The market is shrinking.',
        ],
        'answer': 0, 'hint': 'Compare the store\'s growth to the market\'s growth, not to zero.',
    },
    {
        'id': 'r1', 'dimension': 'reasoning', 'level': 'Interpret', 'difficulty': 2, 'type': 'mcq',
        'prompt': 'All effective managers delegate. Sara does not delegate. Which conclusion is valid?',
        'options': [
            'Sara is not an effective manager.',
            'Sara is an effective manager.',
            'Delegating always makes a manager effective.',
            'Nothing can be concluded.',
        ],
        'answer': 0, 'hint': 'If every effective manager delegates, what must be true of someone who does not?',
    },
    {
        'id': 'd1', 'dimension': 'decision', 'level': 'Choose', 'difficulty': 3, 'type': 'mcq',
        'prompt': 'You must ship a product. Option A: launch now with a known minor bug. Option B: delay 3 months to fix it, missing the season. Customers care most about being on time. Best choice?',
        'options': [
            'Launch now, disclose the minor bug, and patch it shortly after.',
            'Delay 3 months to remove the minor bug.',
            'Cancel the launch entirely.',
            'Launch now but hide the bug from customers.',
        ],
        'answer': 0, 'hint': 'Weigh the size of the bug against what customers value most.',
    },
    {
        'id': 'r2', 'dimension': 'reasoning', 'level': 'Harder follow-up', 'difficulty': 4, 'type': 'mcq',
        'prompt': 'A team doubles output every week. In week 6 it produces 320 units. In which week did it produce 40 units?',
        'options': ['Week 3', 'Week 2', 'Week 4', 'Week 1'],
        'answer': 0, 'hint': 'Halve from 320 backwards: 320 → 160 → 80 → 40. Count the weeks.',
    },
    {
        'id': 'p1', 'dimension': 'problem_solving', 'level': 'Solve unfamiliar', 'difficulty': 4, 'type': 'mcq',
        'prompt': 'Three machines make 3 widgets in 3 minutes. How long for 100 machines to make 100 widgets?',
        'options': ['3 minutes', '100 minutes', '33 minutes', '1 minute'],
        'answer': 0, 'hint': 'Each machine makes 1 widget in 3 minutes, no matter how many machines run in parallel.',
    },
    {
        'id': 't1', 'dimension': 'transfer', 'level': 'Apply to a new case', 'difficulty': 3, 'type': 'open',
        'prompt': 'You learned that "spaced repetition" (reviewing material at increasing intervals) improves memory. In 2-3 sentences, describe how you would apply this idea to preparing for a professional certification exam over 8 weeks.',
        'rubric': 'Full credit: applies spacing correctly — schedules reviews at increasing intervals over the 8 weeks rather than cramming, and connects it to the exam. Partial: mentions reviewing/repetition but not spacing or scheduling. Low: cramming, off-topic, or restates the definition without applying it.',
        'hint': 'Think about WHEN you would review, not just that you would review.',
    },
    {
        'id': 't2', 'dimension': 'transfer', 'level': 'Cognitive challenge', 'difficulty': 5, 'type': 'mcq',
        'prompt': 'A pricing rule that works for physical goods is "lower price → more sales". A software company finds that raising the price of its premium plan increased sales. Which explanation best transfers a NEW principle here?',
        'options': [
            'For some products, a higher price signals higher quality, which can raise demand.',
            'The pricing rule was applied incorrectly and should be reversed.',
            'Software has no marginal cost, so price never affects sales.',
            'Customers always buy the cheapest option available.',
        ],
        'answer': 0, 'hint': 'What can a high price communicate to a buyer about quality?',
    },
]

# Weight of each dimension toward the three headline cognitive scores.
DIMENSION_TO_SCORE = {
    'comprehension': 'knowledge',
    'reasoning': 'reasoning',
    'decision': 'reasoning',
    'problem_solving': 'application',
    'transfer': 'application',
}


def _difficulty_time_baseline(difficulty):
    """Expected seconds for a capable learner at a given difficulty."""
    return {1: 25, 2: 35, 3: 55, 4: 75, 5: 90}.get(difficulty, 45)


def clamp(value, low=0, high=100):
    return max(low, min(high, value))


def compute_scores(responses, open_grades=None, tasks=None):
    """responses: list of dicts {id, correct(bool or None), time_ms, hint_used, confidence(1-3)}.
    open_grades: {task_id: fraction 0..1} for AI-graded open tasks.
    tasks: metadata for the tasks used (AI-generated or built-in CAPACITY_TASKS);
           each needs id, dimension, difficulty, type.
    Returns a dict of 0-100 capacity scores plus supporting signals.
    """
    open_grades = open_grades or {}
    by_id = {t['id']: t for t in (tasks or CAPACITY_TASKS)}
    buckets = {'knowledge': [], 'reasoning': [], 'application': []}
    weighted_correct = 0.0
    weighted_total = 0.0
    speed_ratios = []
    hint_count = 0
    per_item_credit = []
    calibration_gap = []

    for r in responses:
        task = by_id.get(r.get('id'))
        if not task:
            continue
        difficulty = task['difficulty']
        # credit 0..1
        if task['type'] == 'open':
            credit = float(open_grades.get(task['id'], 0.0))
        else:
            credit = 1.0 if r.get('correct') else 0.0
        per_item_credit.append(credit)
        bucket = DIMENSION_TO_SCORE.get(task['dimension'], 'reasoning')
        buckets[bucket].append((credit, difficulty))
        weighted_correct += credit * difficulty
        weighted_total += difficulty

        # speed: faster-than-baseline is good, but cap so rushing wrong answers isn't rewarded
        time_s = max(1.0, float(r.get('time_ms') or 0) / 1000.0)
        ratio = _difficulty_time_baseline(difficulty) / time_s
        speed_ratios.append(min(ratio, 2.0))

        if r.get('hint_used'):
            hint_count += 1

        # confidence calibration: 1 low .. 3 high vs correctness
        conf = r.get('confidence')
        if conf in (1, 2, 3):
            predicted = (conf - 1) / 2.0  # 0, .5, 1
            calibration_gap.append(abs(predicted - credit))

    def bucket_score(items):
        if not items:
            return 0
        num = sum(c * d for c, d in items)
        den = sum(d for _, d in items)
        return int(round(num / den * 100)) if den else 0

    knowledge = bucket_score(buckets['knowledge'])
    reasoning = bucket_score(buckets['reasoning'])
    application = bucket_score(buckets['application'])

    # learning speed 0-100 from average speed ratio (1.0 == on baseline)
    speed = int(round(clamp((sum(speed_ratios) / len(speed_ratios)) * 55, 0, 100))) if speed_ratios else 50

    total_items = len(per_item_credit) or 1
    help_seeking = int(round(hint_count / total_items * 100))

    # consistency: lower spread of per-item credit == more consistent
    mean_credit = sum(per_item_credit) / total_items
    variance = sum((c - mean_credit) ** 2 for c in per_item_credit) / total_items
    consistency = int(round(clamp((1 - variance) * 100)))

    calibration = int(round(clamp((1 - (sum(calibration_gap) / len(calibration_gap))) * 100))) if calibration_gap else 60

    overall = int(round(weighted_correct / weighted_total * 100)) if weighted_total else 0

    return {
        'knowledge': knowledge,
        'reasoning': reasoning,
        'application': application,
        'speed': speed,
        'help_seeking': help_seeking,
        'consistency': consistency,
        'calibration': calibration,
        'overall': overall,
    }


def level_band(scores):
    overall = scores['overall']
    if overall >= 80:
        return 'Advanced'
    if overall >= 55:
        return 'Intermediate'
    return 'Beginner'


# ---------------------------------------------------------------------------
# Strategy archetypes — each maps capacity signals to concrete study "knobs".
# ---------------------------------------------------------------------------

STRATEGIES = {
    'accelerated': {
        'name': 'Accelerated / Challenge-first',
        'tagline': 'You learn fast and reason well — skip the padding and learn by tackling real problems.',
        'knobs': ['Problem-first sequence', 'Larger modules, fewer repeats', 'Project & case work', 'Faster pace'],
    },
    'structured_mastery': {
        'name': 'Structured Mastery',
        'tagline': 'You are thorough and persistent — build rock-solid foundations one step at a time.',
        'knobs': ['Small chunks', 'Master each step before advancing', 'Spaced review', 'Steady pace'],
    },
    'scaffolded': {
        'name': 'Example → Practice',
        'tagline': 'You understand ideas quickly but applying them to new cases needs reps — learn from worked examples, then practise.',
        'knobs': ['Worked examples first', 'Guided practice → independent', 'Frequent applied exercises', 'Transfer drills'],
    },
    'reinforcement': {
        'name': 'Retrieval & Reinforcement',
        'tagline': 'Your accuracy swings between tasks — lock knowledge in with regular retrieval and spacing.',
        'knobs': ['Flashcards on', 'Frequent low-stakes quizzes', 'Spaced repetition', 'Review before advancing'],
    },
    'guided': {
        'name': 'Guided / Supported',
        'tagline': 'A little structure and feedback goes a long way for you — learn with scaffolds and checkpoints.',
        'knobs': ['Step-by-step scaffolds', 'Hints available', 'Regular checkpoints', 'Encouraging pace'],
    },
    'independent': {
        'name': 'Independent / Exploratory',
        'tagline': 'You handle new problems on your own — learn best with open-ended, discovery-led work.',
        'knobs': ['Minimal scaffolding', 'Open-ended challenges', 'Self-directed projects', 'Explore then confirm'],
    },
}


def recommend_strategy(scores):
    """Rule-based best-fit. Returns (key, strategy_dict, rationale)."""
    knowledge = scores['knowledge']
    reasoning = scores['reasoning']
    application = scores['application']
    speed = scores['speed']
    consistency = scores['consistency']
    help_seeking = scores['help_seeking']
    calibration = scores['calibration']

    comprehension = max(knowledge, reasoning)
    gap = comprehension - application  # strong understanding but weak transfer

    scored = []
    # each archetype gets a fit score; highest wins
    scored.append(('accelerated', (speed - 55) + (reasoning - 60) + (application - 60)))
    scored.append(('structured_mastery', (60 - speed) + (consistency - 55) + (10 if help_seeking < 30 else 0)))
    scored.append(('scaffolded', gap * 1.5))
    scored.append(('reinforcement', (60 - consistency) * 1.4))
    scored.append(('guided', (help_seeking - 30) + (60 - calibration)))
    scored.append(('independent', (application - 60) + (30 - help_seeking)))

    scored.sort(key=lambda x: x[1], reverse=True)
    key = scored[0][0]
    strategy = STRATEGIES[key]

    rationale = _rationale(key, scores, gap)
    return key, strategy, rationale


def _rationale(key, scores, gap):
    s = scores
    if key == 'accelerated':
        return f"Fast responses (speed {s['speed']}) with strong reasoning ({s['reasoning']}) and application ({s['application']}) mean you can skip the basics and learn by doing."
    if key == 'structured_mastery':
        return f"You take your time and stay consistent ({s['consistency']}) — a step-by-step path with mastery checkpoints will serve you best."
    if key == 'scaffolded':
        return f"You grasp concepts well but applying them to new cases lags behind (a {gap}-point gap) — worked examples then guided practice will close it."
    if key == 'reinforcement':
        return f"Your accuracy varied across tasks (consistency {s['consistency']}) — spaced retrieval and frequent low-stakes quizzes will make it stick."
    if key == 'guided':
        return f"Some scaffolding and feedback will help you most (you used hints and confidence calibration was {s['calibration']})."
    return f"You solved unfamiliar problems on your own (application {s['application']}) with little help — open-ended, self-directed work suits you."


BAND_ORDER = ['Beginner', 'Intermediate', 'Advanced']


def topic_band_from(fraction):
    """Map difficulty-weighted correctness (0..1) on the topic-knowledge check to
    a starting level."""
    if fraction >= 0.7:
        return 'Advanced'
    if fraction >= 0.4:
        return 'Intermediate'
    return 'Beginner'


def _topic_relevance(course, topic_l, words):
    hay = ' '.join([(course.title or ''), (course.expertise_area or ''),
                    (course.description or ''), (course.sales_copy or '')]).lower()
    area = (course.expertise_area or '').lower()
    score = 0
    if topic_l and topic_l in hay:
        score += 6
    score += sum(3 for w in words if w in area)
    score += sum(1 for w in words if w in hay)
    return score


def build_learning_plan(courses, topic, topic_band):
    """Return a staged Beginner→Intermediate→Advanced roadmap for the chosen
    topic, marking where the learner starts and estimating a timeline.
    `courses` is a list of published Course-like objects.
    """
    topic_l = (topic or '').strip().lower()
    words = [w for w in topic_l.split() if len(w) >= 2]
    scored = [(_topic_relevance(c, topic_l, words), c) for c in courses]
    relevant = [c for s, c in scored if s > 0]
    matched = bool(relevant)
    pool = relevant if relevant else [c for _, c in scored]

    start_index = BAND_ORDER.index(topic_band) if topic_band in BAND_ORDER else 0
    stages = []
    for i, level in enumerate((1, 2, 3)):
        level_courses = [c for c in pool if (c.certificate_level or 1) == level]
        level_courses.sort(key=lambda c: _topic_relevance(c, topic_l, words), reverse=True)
        level_courses = level_courses[:3]
        hours = sum((c.learning_hours or 15) for c in level_courses)
        stages.append({
            'band': BAND_ORDER[i],
            'courses': level_courses,
            'weeks': max(2, round(hours / 5)) if level_courses else 0,
            'status': 'passed' if i < start_index else ('current' if i == start_index else 'upcoming'),
        })
    total_weeks = sum(s['weeks'] for s in stages if s['status'] != 'passed')
    # "covered" = the catalog has a relevant course at the learner's level or above
    # (i.e. a real path forward exists). If not, we offer a custom-built course.
    covered = matched and any(s['courses'] for s in stages if s['status'] in ('current', 'upcoming'))
    return {'stages': stages, 'start': topic_band, 'matched': matched,
            'total_weeks': total_weeks, 'covered': covered}


def suggest_courses(courses, topic, band):
    """Order published Course objects by relevance to the chosen topic and band.
    `courses` is a list of Course-like objects (title, expertise_area, certificate_level, description).
    Returns the top matches (up to 6). Pure ranking — no DB, no side effects.
    """
    topic_l = (topic or '').strip().lower()
    band_target = {'Beginner': 1, 'Intermediate': 2, 'Advanced': 3}.get(band, 1)
    ranked = []
    for course in courses:
        area = (course.expertise_area or '').lower()
        hay = ' '.join([
            (course.title or ''), area,
            (course.description or ''), (course.sales_copy or ''),
        ]).lower()
        score = 0
        if topic_l:
            words = [word for word in topic_l.split() if len(word) >= 2]
            if topic_l in hay:
                score += 50
            score += sum(8 for word in words if word in area)   # matching the expertise area is a strong signal
            score += sum(4 for word in words if word in hay)
        # prefer courses at or just above the learner's band
        clevel = course.certificate_level or 1
        score += max(0, 20 - abs(clevel - band_target) * 8)
        ranked.append((score, course))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [c for score, c in ranked if score > 0][:6] or [c for _, c in ranked[:4]]
