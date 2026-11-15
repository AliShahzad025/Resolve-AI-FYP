"""Builds ml/complaint_seed_dataset.csv: a large SYNTHETIC complaint dataset (English, Roman Urdu, mixed)
labelled with category, urgency and sentiment, for fine-tuning the Resolve AI complaint classifier.

Labelling rules (see ml/LABEL_GUIDE.md):
  * category = the ROOT problem. "Parcel toota hua, paise wapis chahiye" -> damaged_item (not refund_request).
  * urgency high = angry/threatening, money lost, item broken or missing, explicit deadline; low = questions.
  * other = greetings, thanks, acknowledgements, small talk (not a complaint).

How variety is created: each hand-written "problem" is combined with optional requests, greetings,
closings, escalation phrases, emojis, WhatsApp casing and many real Roman Urdu spelling variants.
All rows built from the same problem share a group_id, so the notebook never puts variations of one
problem in both training and test.

Run:  python ml/generate_seed_dataset.py
"""
import csv
import random
import re
from pathlib import Path

random.seed(2026)
VARIANTS_PER_PROBLEM = 28
OTHER_VARIANTS = 24
ESCALATION_RATE = 0.22

# (language, text, urgency, sentiment); {item} -> a product name
PROBLEMS = {
    "damaged_item": [
        ("en", "The {item} arrived broken and the box was torn", "high", "negative"),
        ("en", "My {item} has a big scratch on it", "medium", "negative"),
        ("en", "The {item} stopped working after one day", "medium", "negative"),
        ("en", "The {item} is cracked from the side", "medium", "negative"),
        ("en", "There is a stain on the {item} I received", "medium", "negative"),
        ("en", "The stitching of the {item} is coming off", "medium", "negative"),
        ("en", "The {item} was leaking inside the parcel", "high", "negative"),
        ("roman_urdu", "{item} toota hua aaya hai", "high", "negative"),
        ("roman_urdu", "parcel toota hua mila, andar {item} bhi kharab hai", "high", "negative"),
        ("roman_urdu", "{item} pe scratch hai", "medium", "negative"),
        ("roman_urdu", "ek din mein hi {item} kharab ho gaya", "medium", "negative"),
        ("roman_urdu", "{item} phata hua hai", "medium", "negative"),
        ("roman_urdu", "{item} ki silai khul gayi hai", "medium", "negative"),
        ("roman_urdu", "{item} chal hi nahi raha", "medium", "negative"),
        ("roman_urdu", "{item} pe daagh laga hua hai", "medium", "negative"),
        ("mixed", "{item} damaged condition mein deliver hua hai", "high", "negative"),
        ("mixed", "packing bohat weak thi, {item} crack ho gaya", "medium", "negative"),
        ("mixed", "{item} ka zip work nahi kar raha", "medium", "negative"),
        ("mixed", "defective {item} mila hai", "high", "negative"),
        ("mixed", "{item} ki screen broken hai", "high", "negative"),
        ("mixed", "{item} use karte hi kharab ho gaya, quality bohat bad hai", "medium", "negative"),
    ],
    "wrong_item": [
        ("en", "I ordered a black {item} but received a blue one", "medium", "negative"),
        ("en", "You sent me the wrong {item}", "medium", "negative"),
        ("en", "The parcel had someone else's order inside", "high", "negative"),
        ("en", "I received size small but I ordered large", "medium", "negative"),
        ("en", "One {item} is missing from my order", "high", "negative"),
        ("en", "This is not the {item} I ordered, the design is different", "medium", "negative"),
        ("roman_urdu", "maine kala {item} order kiya tha, neela aa gaya", "medium", "negative"),
        ("roman_urdu", "ghalat {item} bhej diya aap ne", "medium", "negative"),
        ("roman_urdu", "parcel mein kisi aur ka saman hai", "high", "negative"),
        ("roman_urdu", "maine bara size manga tha, chota bhej diya", "medium", "negative"),
        ("roman_urdu", "order mein ek {item} kam hai", "high", "negative"),
        ("roman_urdu", "ye wo {item} nahi hai jo maine order kiya tha", "medium", "negative"),
        ("roman_urdu", "do {item} mangwaye thay, ek hi aaya", "high", "negative"),
        ("mixed", "wrong size bheja hai, maine large order kiya tha", "medium", "negative"),
        ("mixed", "{item} wrong colour mein aaya hai", "medium", "negative"),
        ("mixed", "totally different product aaya hai", "medium", "negative"),
        ("mixed", "order mein ek item missing hai", "high", "negative"),
        ("mixed", "picture wala design nahi hai, dusra {item} bhej diya", "medium", "negative"),
        ("mixed", "mera order kisi aur ke parcel se exchange ho gaya hai", "high", "negative"),
    ],
    "return_exchange": [
        ("en", "The {item} doesn't fit me, can I exchange it for a larger size", "low", "neutral"),
        ("en", "I would like to return the {item}, what is the process", "low", "neutral"),
        ("en", "I don't like the colour of the {item}, can I return it", "low", "neutral"),
        ("en", "How many days do I have to return an item", "low", "neutral"),
        ("en", "I changed my mind about the {item} and want to send it back", "low", "neutral"),
        ("en", "The quality of the {item} is not like the pictures, I want to return it", "medium", "negative"),
        ("roman_urdu", "{item} ka size chota hai, kya exchange ho sakta hai", "low", "neutral"),
        ("roman_urdu", "mujhe ye {item} wapis karna hai", "low", "neutral"),
        ("roman_urdu", "{item} pasand nahi aaya, wapis kar dun", "low", "neutral"),
        ("roman_urdu", "wapis karne ke liye kitne din hote hain", "low", "neutral"),
        ("roman_urdu", "parcel wapis kar do", "low", "neutral"),
        ("roman_urdu", "{item} ka rang badalna hai", "low", "neutral"),
        ("roman_urdu", "quality achi nahi, ye {item} wapis le lein", "medium", "negative"),
        ("mixed", "size issue hai, {item} exchange karna hai", "low", "neutral"),
        ("mixed", "return policy kya hai", "low", "neutral"),
        ("mixed", "exchange request di thi, pickup nahi aaya abhi tak", "medium", "negative"),
        ("mixed", "{item} return karna hai, rider kab aayega pickup ke liye", "low", "neutral"),
        ("mixed", "colour pasand nahi aaya, exchange possible hai", "low", "neutral"),
    ],
    "refund_request": [
        ("en", "I returned the {item} two weeks ago and still have not received my refund", "medium", "negative"),
        ("en", "When will my refund be processed", "medium", "neutral"),
        ("en", "You promised a refund but nothing has come to my account", "high", "negative"),
        ("en", "Can you confirm my refund was sent", "low", "neutral"),
        ("en", "My refund amount is less than what I paid", "high", "negative"),
        ("roman_urdu", "mera refund abhi tak nahi aaya", "medium", "negative"),
        ("roman_urdu", "refund kab tak milega", "medium", "neutral"),
        ("roman_urdu", "aap ne refund ka wada kiya tha, paise nahi aaye", "high", "negative"),
        ("roman_urdu", "bas bata dein refund process hua ya nahi", "low", "neutral"),
        ("roman_urdu", "refund mein paise kam aaye hain", "high", "negative"),
        ("roman_urdu", "{item} wapis kiye do hafte ho gaye, paise kab milenge", "medium", "negative"),
        ("mixed", "mera refund still pending hai", "medium", "negative"),
        ("mixed", "refund kab credit hoga account mein", "medium", "neutral"),
        ("mixed", "refund ka koi update nahi, third time message kar raha hun", "high", "negative"),
        ("mixed", "refund status check kar ke batayein", "low", "neutral"),
        ("mixed", "return accept ho gaya tha lekin refund nahi mila", "medium", "negative"),
    ],
    "delivery_issue": [
        ("en", "Where is my order, it was supposed to arrive three days ago", "medium", "negative"),
        ("en", "Can you share the tracking number for my {item}", "low", "neutral"),
        ("en", "My order shows delivered but I never received it", "high", "negative"),
        ("en", "When will my order be delivered", "low", "neutral"),
        ("en", "The rider never called and returned my parcel", "medium", "negative"),
        ("en", "My order has been stuck in transit for a week", "medium", "negative"),
        ("roman_urdu", "mera order kahan hai, teen din pehle aana tha", "medium", "negative"),
        ("roman_urdu", "order kab tak aayega", "low", "neutral"),
        ("roman_urdu", "tracking number bhej dein", "low", "neutral"),
        ("roman_urdu", "order pohanch gaya likha hai lekin mujhe mila hi nahi", "high", "negative"),
        ("roman_urdu", "rider ne call nahi kiya aur parcel wapis le gaya", "medium", "negative"),
        ("roman_urdu", "ek hafte se order raste mein hai", "medium", "negative"),
        ("roman_urdu", "mera order abhi tak nahi pohancha", "medium", "negative"),
        ("mixed", "delivery bohat late hai, one week ho gaya", "medium", "negative"),
        ("mixed", "courier wala bol raha hai address nahi mila", "medium", "neutral"),
        ("mixed", "order shipped dikha raha hai lekin koi update nahi", "medium", "negative"),
        ("mixed", "parcel status delivered hai par mujhe receive nahi hua", "high", "negative"),
        ("mixed", "delivery date kya hai mere order ki", "low", "neutral"),
        # "delay" words must mean delivery (the v2 model linked "delayed" to cancellation)
        ("en", "Why is my order delayed", "medium", "negative"),
        ("en", "My delivery is delayed again", "medium", "negative"),
        ("en", "Why is my parcel taking so long", "medium", "negative"),
        ("en", "My order is late, what is the reason for the delay", "medium", "negative"),
        ("en", "The delivery has been delayed for five days now", "high", "negative"),
        ("roman_urdu", "mera order late kyun hai", "medium", "negative"),
        ("roman_urdu", "order abhi tak kyun nahi aaya", "medium", "negative"),
        ("roman_urdu", "itni der kyun lag rahi hai order mein", "medium", "negative"),
        ("roman_urdu", "parcel aane mein bohat der ho gayi hai", "medium", "negative"),
        ("mixed", "delivery mein delay kyun ho raha hai", "medium", "negative"),
        ("mixed", "order delayed hai, kab tak milega", "medium", "negative"),
        ("mixed", "why my order is late, bohat din ho gaye", "medium", "negative"),
    ],
    "payment_issue": [
        ("en", "I was charged twice for the same order", "high", "negative"),
        ("en", "I paid by bank transfer but my order still shows unpaid", "medium", "negative"),
        ("en", "Can I pay cash on delivery", "low", "neutral"),
        ("en", "The rider asked for more money than the order amount", "high", "negative"),
        ("en", "My card was charged but the order failed", "high", "negative"),
        ("roman_urdu", "mere account se do dafa paise kat gaye", "high", "negative"),
        ("roman_urdu", "maine paise bhej diye thay lekin order unpaid dikha raha hai", "medium", "negative"),
        ("roman_urdu", "kya cash on delivery ho sakti hai", "low", "neutral"),
        ("roman_urdu", "rider ne order se zyada paise maange", "high", "negative"),
        ("roman_urdu", "paise kat gaye lekin order nahi bana", "high", "negative"),
        ("mixed", "card se payment ho gayi lekin order confirm nahi hua", "medium", "negative"),
        ("mixed", "COD ka option show nahi ho raha", "low", "neutral"),
        ("mixed", "double payment deduct hui hai", "high", "negative"),
        ("mixed", "easypaisa se payment ki hai, confirm kar dein", "low", "neutral"),
        ("mixed", "jazzcash payment fail ho gayi lekin balance kat gaya", "high", "negative"),
    ],
    "cancellation": [
        ("en", "Please cancel my order, I placed it by mistake", "medium", "neutral"),
        ("en", "I want to cancel my order before it ships", "medium", "neutral"),
        ("en", "Why was my order cancelled without telling me", "medium", "negative"),
        ("en", "Cancel my order, it has been delayed for two weeks", "high", "negative"),
        ("roman_urdu", "mera order cancel kar dein, ghalti se ho gaya", "medium", "neutral"),
        ("roman_urdu", "order rok dein, abhi na bhejein", "medium", "neutral"),
        ("roman_urdu", "mujhe bataye baghair order kyun cancel kiya", "medium", "negative"),
        ("roman_urdu", "abhi ke abhi order cancel karo, do hafte se intezar hai", "high", "negative"),
        ("mixed", "order cancel karna hai, plan change ho gaya", "medium", "neutral"),
        ("mixed", "cancel request di thi phir bhi order dispatch ho gaya", "medium", "negative"),
        ("mixed", "mera order auto cancel kyun ho gaya", "medium", "negative"),
        ("mixed", "please mera order cancel kar dein jaldi", "medium", "neutral"),
    ],
    "general_inquiry": [
        ("en", "Is the {item} available in size large", "low", "neutral"),
        ("en", "Do you deliver to Quetta", "low", "neutral"),
        ("en", "What material is this {item} made of", "low", "neutral"),
        ("en", "Do you have this {item} in red", "low", "neutral"),
        ("en", "What are your shop timings", "low", "neutral"),
        ("roman_urdu", "kya ye {item} bara size mein mil sakta hai", "low", "neutral"),
        ("roman_urdu", "kya aap Quetta mein saman bhejte hain", "low", "neutral"),
        ("roman_urdu", "ye {item} kis cheez ka bana hai", "low", "neutral"),
        ("roman_urdu", "{item} ki qeemat kya hai", "low", "neutral"),
        ("roman_urdu", "kya ye lal rang mein bhi hai", "low", "neutral"),
        ("mixed", "{item} ka stock kab aayega", "low", "neutral"),
        ("mixed", "same day delivery available hai Lahore mein", "low", "neutral"),
        ("mixed", "{item} ka price kya hai, discount code chalega", "low", "neutral"),
        ("mixed", "size chart bhej dein please", "low", "neutral"),
        ("mixed", "sale kab start ho rahi hai", "low", "neutral"),
    ],
    "other": [
        ("en", "Hello", "low", "neutral"), ("en", "Hi, how are you", "low", "neutral"),
        ("en", "Thank you so much", "low", "positive"), ("en", "Ok thanks", "low", "neutral"),
        ("en", "Got my parcel, thank you, very happy", "low", "positive"), ("en", "Good morning", "low", "neutral"),
        ("en", "Great service, will order again", "low", "positive"), ("en", "Okay noted", "low", "neutral"),
        ("roman_urdu", "assalam o alaikum", "low", "neutral"), ("roman_urdu", "shukriya bohat acha laga", "low", "positive"),
        ("roman_urdu", "theek hai", "low", "neutral"), ("roman_urdu", "jee", "low", "neutral"),
        ("roman_urdu", "kya haal hai", "low", "neutral"), ("roman_urdu", "parcel mil gaya, bohat acha hai", "low", "positive"),
        ("roman_urdu", "allah hafiz", "low", "neutral"), ("roman_urdu", "aap ki service bohat achi hai", "low", "positive"),
        ("mixed", "ok theek hai thanks", "low", "neutral"), ("mixed", "hello koi hai", "low", "neutral"),
        ("mixed", "parcel receive ho gaya thank you", "low", "positive"), ("mixed", "nice quality hai, love it", "low", "positive"),
        ("mixed", "AOA, how are you", "low", "neutral"), ("mixed", "done, thank you bhai", "low", "positive"),
        ("en", "The {item} is beautiful, exactly like the picture", "low", "positive"),
        ("en", "Fast delivery and great packing, thank you", "low", "positive"),
        ("en", "Loved the {item}, my family liked it too", "low", "positive"),
        ("roman_urdu", "{item} bohat khoobsurat hai, shukriya", "low", "positive"),
        ("roman_urdu", "delivery bohat jaldi ho gayi, bohat shukriya", "low", "positive"),
        ("roman_urdu", "maza aa gaya, dobara order karunga", "low", "positive"),
        ("mixed", "{item} ki quality amazing hai, thank you so much", "low", "positive"),
        ("mixed", "packing bohat achi thi, highly recommended", "low", "positive"),
        ("mixed", "best online shop hai, keep it up", "low", "positive"),
    ],
}

ITEMS = ["kurta", "shoes", "lawn suit", "watch", "headphones", "handbag", "jacket", "perfume", "dupatta",
         "mobile cover", "earbuds", "shalwar kameez", "sandals", "charger", "bedsheet", "kids suit"]

OPENERS = {
    "en": ["", "", "Hi, ", "Hello, ", "Dear team, ", "Excuse me, "],
    "roman_urdu": ["", "", "Assalam o alaikum, ", "AOA ", "Bhai ", "Sir ", "Ji "],
    "mixed": ["", "", "Hi, ", "AOA, ", "Sir please ", "Hello bhai "],
}
CLOSERS = {
    "en": ["", "", ". Order #{n}", ". Please help", ". Kindly reply soon", "?"],
    "roman_urdu": ["", "", ". Order number {n} hai", ". Jaldi batayein", ". Meharbani karein", "?"],
    "mixed": ["", "", ". Order #{n}", ". Please jaldi reply karein", ". Thanks", "?"],
}
# Requests that can follow a problem without changing its category (root problem rule)
REQUESTS = {
    ("damaged_item", "wrong_item"): {
        "en": ["I want a refund", "Please replace it", "Please send a new one", "I want my money back"],
        "roman_urdu": ["paise wapis chahiye", "badal dein", "naya bhej dein", "mere paise wapis karo"],
        "mixed": ["refund chahiye", "replacement bhej dein", "exchange kar dein", "money back chahiye"],
    },
    ("delivery_issue",): {
        "en": ["When will it arrive", "Please send it quickly", "Please check"],
        "roman_urdu": ["kab tak aayega", "jaldi bhejein", "check kar ke batayein"],
        "mixed": ["kab tak deliver hoga", "please jaldi bhejein", "status batayein"],
    },
    ("payment_issue", "refund_request"): {
        "en": ["Please check my account", "Please return my money"],
        "roman_urdu": ["mere paise wapis karein", "check kar lein"],
        "mixed": ["please check karein", "amount reverse karein"],
    },
}
# Escalations turn a complaint into high urgency + negative sentiment
ESCALATIONS = {
    "en": ["This is unacceptable!", "I will leave a bad review.", "Worst service ever.", "Fix this today.",
           "Otherwise I will complain to the consumer court.", "I am very angry!!!", "Third time I am messaging!"],
    "roman_urdu": ["warna main review dunga", "bohat ghatiya service hai", "aaj hi theek karo", "consumer court jaunga",
                   "aakhri dafa keh raha hun", "bohat ghussa aa raha hai", "teesri dafa message kar raha hun"],
    "mixed": ["very bad service hai", "warna bad review dunga", "aaj hi solve karo", "worst experience hai",
              "consumer court mein complaint karunga", "bohat disappointed hun!!!", "third time message kar raha hun"],
}
EMOJI = {
    "angry": ["", "", " 😡", " 😠", " 😤", "!!!"],
    "negative": ["", "", "", " 😕", " 😞", " 🙏"],
    "neutral": ["", "", "", " 🙏", " 🙂"],
    "positive": ["", " 😊", " ❤️", " 👍", " 🙏"],
}

# Real Roman Urdu spelling variations seen in WhatsApp messages
SPELLINGS = {
    "hai": ["hai", "hy", "he", "ha"], "hain": ["hain", "hn", "hen"], "nahi": ["nahi", "nai", "nhi", "nahin", "ni"],
    "hua": ["hua", "hoa", "huwa", "howa"], "hui": ["hui", "hoi", "huwi"], "aaya": ["aaya", "aya", "aayaa"],
    "aaye": ["aaye", "aye", "aae"], "paise": ["paise", "paisay", "pese", "pesay", "paisa"],
    "chahiye": ["chahiye", "chaiye", "chahye", "chahiay", "chaheye"], "wapis": ["wapis", "wapas", "wapus", "waapis"],
    "bohat": ["bohat", "bohot", "bahut", "boht", "bht"], "kharab": ["kharab", "khrab", "kharaab"],
    "ghalat": ["ghalat", "galat", "glt"], "jaldi": ["jaldi", "jldi", "jaldee"], "kya": ["kya", "kia", "kiya", "kyaa"],
    "toota": ["toota", "tuta", "toot", "toota"], "kar": ["kar", "kr"], "karein": ["karein", "karen", "krein", "kren"],
    "dein": ["dein", "den", "do", "dain"], "mera": ["mera", "mra", "mera"], "mujhe": ["mujhe", "mjhe", "mujhay"],
    "abhi": ["abhi", "abi", "abhee"], "tak": ["tak", "tk"], "kab": ["kab", "kb"], "raha": ["raha", "rha"],
    "please": ["please", "plz", "pls", "plzz"], "thanks": ["thanks", "thnx", "thx"], "order": ["order", "ordr", "order"],
    "kahan": ["kahan", "kahaan", "kaha"], "theek": ["theek", "thik", "theak"], "shukriya": ["shukriya", "shukria"],
    "hafte": ["hafte", "haftay"], "lekin": ["lekin", "lkin", "magar"], "bhej": ["bhej", "bhaij", "bej"],
}

TEMPLATE_VARIABLES = re.compile(r"\{(item|n)\}")


def respell(text: str) -> str:
    def swap(match):
        word = match.group(0)
        options = SPELLINGS.get(word.lower())
        return random.choice(options) if options and random.random() < 0.5 else word
    return re.sub(r"[A-Za-z]+", swap, text)


def typo(text: str) -> str:
    """Occasionally drop or double one letter in a longer word (keyboard slips)."""
    words = text.split(" ")
    long_words = [i for i, w in enumerate(words) if len(w) > 5 and w.isalpha()]
    if not long_words:
        return text
    i = random.choice(long_words)
    w = words[i]
    j = random.randrange(1, len(w) - 1)
    words[i] = w[:j] + w[j + 1:] if random.random() < 0.5 else w[:j] + w[j] + w[j:]
    return " ".join(words)


def requests_for(category: str, lang: str) -> list[str]:
    for cats, by_lang in REQUESTS.items():
        if category in cats:
            return by_lang[lang]
    return []


def build_row(category: str, lang: str, text: str, urgency: str, sentiment: str) -> tuple[str, str, str]:
    body = text.replace("{item}", random.choice(ITEMS))
    reqs = requests_for(category, lang)
    if reqs and random.random() < 0.55:
        body = f"{body}, {random.choice(reqs)}"
    escalate = category != "other" and sentiment != "positive" and random.random() < ESCALATION_RATE
    if escalate:
        body = f"{body}. {random.choice(ESCALATIONS[lang])}"
        urgency, sentiment = "high", "negative"
    if category == "other":
        out = body
    else:
        out = random.choice(OPENERS[lang]) + body + random.choice(CLOSERS[lang]).replace("{n}", str(random.randint(1001, 9999)))
    if lang in ("roman_urdu", "mixed") or random.random() < 0.3:
        out = respell(out)
    if random.random() < 0.12:
        out = typo(out)
    mood = "angry" if urgency == "high" and sentiment == "negative" else sentiment
    out += random.choice(EMOJI.get(mood, EMOJI["neutral"]))
    r = random.random()
    if r < 0.35:
        out = out.lower()
    elif r < 0.45:
        out = re.sub(r"[.,?]", "", out)
    elif out[:1].islower():
        out = out[0].upper() + out[1:]
    return re.sub(r"\s+", " ", out).strip(), urgency, sentiment


def main() -> None:
    rows, seen = [], set()
    for category, problems in PROBLEMS.items():
        for pid, (lang, text, urgency, sentiment) in enumerate(problems):
            group = f"{category}-{pid:02d}"
            made, attempts = 0, 0
            target = VARIANTS_PER_PROBLEM if category != "other" else OTHER_VARIANTS
            while made < target and attempts < target * 6:
                attempts += 1
                body, u, s = build_row(category, lang, text, urgency, sentiment)
                if body.lower() in seen:
                    continue
                seen.add(body.lower())
                rows.append({"text": body, "category": category, "urgency": u, "sentiment": s,
                             "language": lang, "group_id": group, "source": "synthetic_seed_v2"})
                made += 1
    random.shuffle(rows)
    out = Path(__file__).with_name("complaint_seed_dataset.csv")
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    from collections import Counter
    print(f"wrote {len(rows)} rows from {sum(len(p) for p in PROBLEMS.values())} problems -> {out}")
    for col in ("category", "language", "urgency", "sentiment"):
        print(col, dict(Counter(r[col] for r in rows)))


if __name__ == "__main__":
    main()
