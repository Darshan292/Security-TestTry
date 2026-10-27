"""Is a word an ordinary English word? (general linguistic knowledge, used to tell name words from other words)

The word list (data/english_lexicon.txt.gz, built by scripts/build_lexicon.py) is the WordNet lemma index plus
irregular forms and English stop words: 83k lower-case words. A word is English when it, or a regular inflection
of it (plural, past tense, -ing, -ly, comparative, possessive), is in the list or in the small hand-written lists
below (chat abbreviations, forms of address, Hinglish function words, HR and software vocabulary that WordNet does
not have). Personal names are mostly NOT in the list ("Shalini", "Venkataraman", "Phadnis"); a minority are
("Rose", "Will", "Shah"), which the callers accept as misses rather than over-masking ordinary words.
"""
from __future__ import annotations

import functools
import gzip
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "data" / "english_lexicon.txt.gz"

# Not in WordNet but never a personal name in HR text.
EXTRA = frozenset("""
hey hii hiii hlo helo pls plz plss pl thx thnx thnks thanx ty tq tks fyi asap eod eta wfh wfo ooo btw imo pfa pfb
okay ok okk ya yaa yeah yep yup nope hmm hmmm umm noted pls. regards rgds brgds kr wr br
bhai bhaiya bhaiyya bhayya didi yaar yar ji jee sirji madamji mam maam bro bruh dude guys folks dost sab log logo
kya kyu kyun kyon kaise kaisa kaun kahan kab kitna kitni nahi nahin nai haan han acha accha achha theek thik
hai hain tha thi hoga hogi karo kar karna karke kardo kijiye dijiye bata batao bolo dekh dekho chahiye mera meri
mere tera teri tumhara aap aapka aapki apna apni hum hamara humara unka unki uska uski iska iski woh wo yeh ye
abhi kal aaj parso jaldi please. bas sirf aur ya bhi toh to na ki ka ke ko se me mein par pe wala wali wale
admin admins ops devops secops finops hrops sre qa uat prod ui ux app apps api apis sdk cli url urls uri http
https www json xml yaml csv tsv sql nosql db dbs aws gcp azure okta jira confluence slack github gitlab bitbucket
jenkins docker kubectl kubernetes linux unix bash python java javascript typescript golang repo repos webhook
webhooks login logins logout signin signup onboarding offboarding inbox outbox gmail outlook whatsapp
hrms hris hrbp hrbps bgv kyc uan esic esi epf epfo pf vpf nps gratuity tds itr ctc lpa pan aadhaar aadhar otp pin
lop cl sl el pl comp-off compoff wfh payslip payslips payroll reimbursement fnf ffs noc ncr bpo kpo mnc pvt ltd llp
inc corp co dept mgr sr jr exec asst assoc
""".split())

_SUFFIXES = (("ies", "y"), ("ied", "y"), ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"), ("ing", ""), ("ing", "e"),
             ("ly", ""), ("er", ""), ("er", "e"), ("est", ""), ("ness", ""), ("ment", ""))


@functools.lru_cache(maxsize=1)
def words() -> frozenset[str]:
    with gzip.open(_PATH, "rt", encoding="utf-8") as f:
        return frozenset(f.read().split())


@functools.lru_cache(maxsize=65536)
def is_english(word: str) -> bool:
    """True for an ordinary English (or chat / HR vocabulary) word, in any case."""
    w = word.lower().strip("'’.")
    if w.endswith(("'s", "’s")):
        w = w[:-2]
    if not w:
        return False
    lex = words()
    if w in lex or w in EXTRA:
        return True
    if "-" in w:
        return all(is_english(p) for p in w.split("-") if p)
    for suf, rep in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3 and (w[:-len(suf)] + rep) in lex:
            return True
    return False


# Words that end a name when they follow it in a capitalised run ("Leena Fernandes Head of ...", "Kiran Bhatt Joined",
# "Ajay Kulkarni Offer Letter"): roles, departments, documents and HR actions, time words, function words and verb
# forms. Unlike is_english, this leaves out English words that are also surnames ("Bose", "Shah", "Gill"), so a cue
# that trims with it keeps such a surname.
TITLE_WORDS = frozenset("""
head heads manager managers lead leads director directors officer executive associate analyst engineer intern
consultant partner president chief senior junior principal assistant coordinator specialist supervisor administrator
admin hr payroll finance accounts accounting legal compliance operations ops treasury talent acquisition recruitment
recruiting rewards benefits learning development sales marketing support service services team teams department dept
division unit group office desk helpdesk committee board panel policy policies leave leaves request requests letter
letters offer offers draft form forms report reports review reviews approval approvals notice memo update updates note
notes summary ticket tickets case cases claim claims payslip payslips salary bonus appraisal transfer exit joining
onboarding relieving resignation termination increment promotion process portal system meeting call agenda minutes
schedule plan proposal application document documents file files record records statement certificate confirmation
regarding re subject fwd fw cc today tomorrow yesterday tonight morning evening night week month year quarter
monday tuesday wednesday thursday friday saturday sunday january february march april june july august september
october november december jan feb mar apr jun jul aug sep sept oct nov dec ltd limited pvt private inc corp
corporation llp company bank technologies technology solutions systems consulting labs group india
""".split())


@functools.lru_cache(maxsize=16384)
def ends_name(word: str) -> bool:
    """A capitalised word after a name that is not part of it: a TITLE_WORDS word, an English function word, or an
    -ed / -ing / -s verb form of an English word ("Joined", "Presented", "Wants")."""
    w = word.lower().strip("'’.,;:")
    if not w:
        return True
    if w in TITLE_WORDS or w in _FUNCTION:
        return True
    lex = words()
    for suf, rep in (("ed", ""), ("ed", "e"), ("ied", "y"), ("ing", ""), ("ing", "e"), ("s", ""), ("es", "")):
        if w.endswith(suf) and len(w) - len(suf) >= 3 and (w[:-len(suf)] + rep) in lex and w not in lex:
            return True
    return w.endswith(("ed", "ing")) and len(w) > 5 and w in lex


_FUNCTION = frozenset("""
a an the and or but nor so yet for of to in on at by with from into onto about above below after before over under
between through during without within along across behind beyond near off out up down is are was were be been being
am has have had do does did will would shall should can could may might must this that these those there here who
whom whose which what when where why how all any both each every some such no not only own same than too very just
also again further then once please kindly pls thanks regards dear hi hello
""".split())

