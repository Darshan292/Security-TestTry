"""Synthetic gold corpus generator for the privacy-gateway detection evaluation.

Authored independently of the detectors (EVALUATION_PLAN.md rule 2).  Every span
is computed *by construction*: text is assembled through ``Builder`` which knows
the offset of each sensitive value as it is appended.  Nothing is ever located by
searching the finished string.

    python -m eval.synth.generate --out eval/data [--seed 1337]

Stdlib only.  Deterministic for a given (seed, GENERATOR_VERSION).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import random
import re
import string
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

GENERATOR_VERSION = "1.0.0"
SPLITS = ("dev", "tune", "test")
SLICES = ("hr_free_text", "hris_structured", "integration_debug", "negatives")
TYPES = ("PERSON", "EMAIL", "PHONE", "AADHAAR", "PAN", "BANK_ACCOUNT", "IFSC",
         "CARD", "UPI_ID", "SECRET", "EMPLOYEE_ID", "COMPENSATION", "DATE_OF_BIRTH")
# Minimum positive spans per type in the TEST split (EVALUATION_PLAN "Statistical power").
TEST_MIN = {t: 150 for t in TYPES}
TEST_MIN.update({"PERSON": 400, "COMPENSATION": 200, "DATE_OF_BIRTH": 150})
SPLIT_MULT = {"dev": 3, "tune": 1, "test": 1}   # min-count multiplier for top-up
BASE_COUNTS = {   # items per split per slice before top-up (test/tune; dev is 3x)
    "hr_free_text": 500, "hris_structured": 320, "integration_debug": 170, "negatives": 250,
}
TENANT_DOMAIN = "kestrelsys.in"

# --------------------------------------------------------------------------- names
# region -> M / F first names and L last names.  Kept in-code, several hundred each.
NAMES = {
    "north": {
        "M": """Rahul Amit Vikas Sandeep Rajesh Manoj Ankit Rohit Deepak Sachin Vivek Ashish Pankaj Naveen Mukesh
        Sunil Anil Vinod Ramesh Suresh Gaurav Nitin Kunal Mohit Varun Tarun Yogesh Harsh Akhil Lokesh Devendra
        Mahendra Rakesh Sanjay Ajay Vijay Alok Arvind Bhupendra Chetan Dinesh Girish Hemant Jitendra Kailash Lalit
        Mayank Neeraj Pradeep Rajat Saurabh Shivam Tushar Utkarsh Vishal Yash Abhinav Aditya Ishaan Kartik Lakshya
        Piyush Prateek Randhir Satyendra Umesh Rajeev Brijesh Chandrakant Dharmendra Hitesh""",
        "F": """Priya Neha Pooja Sneha Anjali Ritu Shweta Kavita Sunita Meena Rekha Seema Nidhi Swati Ruchi Divya
        Shikha Preeti Renu Anita Sarita Poonam Manisha Garima Isha Jyoti Kritika Komal Megha Nisha Payal Radhika
        Sakshi Tanvi Vandana Aarti Bhavna Charu Deepika Ekta Gunjan Harshita Ira Juhi Khushboo Lata Mansi Namrata
        Parul Richa Sonal Trisha Urvashi Vaishali Yamini Alka Bindu Chhavi Rashi Sushila Vineeta""",
        "L": """Sharma Verma Gupta Agarwal Mishra Pandey Tiwari Dubey Shukla Srivastava Yadav Chauhan Rathore Tomar
        Bansal Goyal Mittal Jindal Saxena Kapoor Malhotra Khanna Mehra Chopra Bhardwaj Joshi Rawat Negi Bisht
        Thakur Kashyap Tyagi Chaturvedi Awasthi Dwivedi Garg Singhal Bhatt Nautiyal Uniyal Kumar Jha Sinha Prasad
        Rajput Solanki Shekhawat Rathi Bajpai Chandra Dixit Gaur Kaushik Lal Mathur Nagar Ojha Sahu Sood Tandon
        Vaish Vyas Khare Rastogi Sethia Kesarwani Pathak Upadhyay""",
    },
    "south": {
        "M": """Venkatesh Karthik Arun Srinivas Murali Prakash Balaji Gopal Harish Krishnan Lakshmanan Mahesh Naresh
        Raghavan Sathish Senthil Vignesh Bharath Chandran Dhanush Ganesh Hari Jagadish Kumaran Madhavan
        Narayanan Pranav Rajkumar Sekhar Sundar Thiruvengadam Uday Vasudevan Yuvraj Ashwin Dinakaran Elango
        Gokul Hemanth Ilango Jayaram Kishore Manikandan Nagarajan Pavan Ravi Saravanan Tharun Vamsi Vishnu
        Sreenivas Chaitanya Raghu Sudhakar Anirudh Badri Gurunath Kamalakar Lingaraj Mohan Nataraj Rangan
        Shankar Vasu Vinayagam""",
        "F": """Lakshmi Anitha Deepa Kavya Meenakshi Revathi Shanthi Sowmya Swathi Vidya Bhuvaneswari Chitra Gayathri
        Hema Indira Janani Kalyani Lavanya Malathi Nandini Padma Radha Saranya Thenmozhi Uma Vasanthi Yamuna
        Aishwarya Amrutha Archana Harini Keerthi Madhavi Nithya Pavithra Rajeshwari Sandhya Sudha Tejaswini
        Usha Vaishnavi Anusha Bhargavi Charitha Geetha Padmavathi Sirisha Soumya Bhanumathi Dhanalakshmi
        Jayanthi Kamala Nirmala Sumathi Tharani Vimala""",
        "L": """Iyer Iyengar Nair Menon Pillai Reddy Naidu Rao Subramanian Venkataraman Balasubramanian
        Chandrasekhar Sundaram Gowda Hegde Shetty Kamath Prabhu Pai Achari Chettiar Mudaliar Gounder Thevar
        Namboothiri Warrier Kurup Nambiar Unnithan Shenoy Poojary Acharya Rajagopalan Srinivasan Natarajan
        Parthasarathy Ramaswamy Swaminathan Vaidyanathan Seshadri Anantharaman Sastry Murthy Raju Chowdary
        Goud Setty Bhandary Naik Krishnamurthy Ranganathan Varadarajan Vishwanathan Ayyar Muthu Pandian
        Sivakumar Kannan""",
    },
    "east": {
        "M": """Subhash Sourav Debashish Arnab Abhijit Partha Tapan Subrata Sudip Anirban Biswajit Dipankar Pranab
        Soumitra Rajib Sabyasachi Ujjal Amitabh Bikash Chandan Dhruba Indranil Jayanta Kaushik Mrinmoy Nilanjan
        Prasenjit Samir Tanmoy Ashok Bibhuti Debasis Harekrishna Manas Niranjan Pratap Sanjib Santosh Trilochan
        Chittaranjan Arijit Barun Gautam Kalyan Pinaki Ritwik Shubhankar Somnath Sumit""",
        "F": """Moumita Sutapa Rina Mitali Debjani Sharmila Sanchita Paromita Tanushree Ipsita Madhumita Piyali Rupa
        Susmita Anindita Barnali Chandana Dolon Jharna Kakoli Mausumi Nandita Pubali Ranjita Shreya Swagata
        Tapasi Arpita Bidisha Bandana Smita Sasmita Pravati Sujata Manjulika Jyotirmayee Aparna Basabi
        Devyani Mahua Sreeparna""",
        "L": """Banerjee Mukherjee Chatterjee Ganguly Chakraborty Bose Sen Das Ghosh Dey Roy Sarkar Bhattacharya
        Majumdar Sengupta Basu Mitra Guha Pal Halder Mondal Biswas Saha Dasgupta Lahiri Nandi Mohanty Patnaik
        Pradhan Panda Behera Nayak Swain Sahoo Mahapatra Rout Jena Tripathy Padhi Chakrabarti Bandyopadhyay
        Mukhopadhyay Gangopadhyay Bhowmick Sanyal Ray Kar Samanta Maity""",
    },
    "west": {
        "M": """Nilesh Prashant Swapnil Omkar Rutvik Chinmay Hrishikesh Jayesh Kalpesh Ketan Mehul Nirav Paresh Snehal
        Tejas Vatsal Bhavesh Chirag Darshan Dhaval Harshad Jignesh Kirit Mitesh Nikunj Parth Sagar Viral Amol
        Avinash Datta Mangesh Pravin Rohan Sameer Shantanu Sushant Vaibhav Vinayak Siddhesh Aniket Bhushan
        Dnyanesh Gajanan Kedar Mandar Nachiket Rajendra Sudhir Tanmay Yogendra""",
        "F": """Shraddha Bhakti Dipali Gauri Hetal Janvi Kajal Mamta Nayana Pallavi Rashmi Shilpa Sushma Trupti
        Vrushali Zarna Aditi Anagha Madhuri Mrunal Neelam Prachi Rutuja Sayali Tejal Vidhi Urmila Dhara Foram
        Khyati Krupa Ketaki Manasi Mugdha Pradnya Rasika Sanika Smruti Vaidehi Yogita""",
        "L": """Deshmukh Patil Kulkarni Deshpande Jadhav Pawar Shinde Gaikwad Kadam More Bhosale Chavan Salunkhe
        Sawant Thakare Wagh Kelkar Apte Gokhale Bhide Phadke Ranade Sathe Patel Shah Mehta Desai Parikh
        Thakkar Vora Modi Gandhi Doshi Bhavsar Jani Pandya Gawde Parab Dalvi Mhatre Kamble Waghmare Ghadge
        Borkar Tendulkar Joglekar Karnik Limaye Bapat Barve Purohit Sheth Zaveri Kothari Jhaveri""",
    },
    "northeast": {
        "M": """Bikram Dhiraj Jiten Kamal Nabajyoti Pranjal Rupam Utpal Biren Ibomcha Thoiba Lalthlamuana
        Zoramthanga Vanlalruata Keneisezo Neiphiu Imtiwapang Tenzing Pema Dorjee Sonam Karma Wangchuk Lhendup
        Rajen Hemen Bhaskar Dipen Jayanta Mridul Nayan Phanindra Robin Tapash Wangdi Chonzom Temjen Alemtemshi""",
        "F": """Ankita Bhaswati Dipti Jonali Mousumi Nirmali Rimjhim Ranjana Bobita Sanatombi Thoibi Memcha
        Lalremsiami Vanlalhriati Lalnunmawii Neilhouvi Vizolhou Yangchen Dolma Tashi Kunzang Bornali Manashi
        Nayanmoni Ibemhal Anupama Bandita Champa Dimple Juri Monalisa""",
        "L": """Borah Bora Gogoi Hazarika Kalita Saikia Barman Phukan Singha Thangjam Laishram Oinam Ningthoujam
        Khumujam Meitei Sangma Marak Momin Lyngdoh Syiem Kharkongor Nongkynrih Zote Hmar Chakma Angami Sema
        Lotha Chishi Sherpa Bhutia Lepcha Tamang Gurung Limbu Haokip Kipgen Nongrum Khonglah Buragohain
        Neog Baruah Sonowal Doley Rabha""",
    },
    "muslim": {
        "M": """Mohammed Imran Salman Faisal Arif Irfan Javed Nadeem Rashid Tariq Zubair Aamir Adil Danish Farhan
        Gulzar Haroon Ibrahim Junaid Kabir Mustafa Naseer Omar Parvez Qasim Rizwan Sajid Shahid Tabish Umar
        Wasim Yusuf Zaheer Akram Bilal Feroz Hamid Idris Jamal Khalid Mansoor Nawaz Owais Riyaz Sohail Talha
        Waseem Aslam Ehsan Fahad Anwar Asif Azhar Firdaus Hussain Zakir""",
        "F": """Ayesha Fatima Zainab Shabana Nasreen Rukhsar Shaista Sana Farah Mehnaz Nazia Rubina Sabiha Tabassum
        Uzma Zoya Afreen Bushra Gulnaz Hina Iram Kausar Lubna Mariam Noor Parveen Rehana Saba Shagufta Tahira
        Zeenat Alina Dilshad Farzana Ghazala Humera Ishrat Jasmin Nuzhat Rabia Samina Shaheen""",
        "L": """Khan Ahmed Ansari Sheikh Siddiqui Qureshi Malik Mirza Pathan Syed Ali Rahman Shaikh Baig Farooqui
        Naqvi Rizvi Zaidi Abbasi Bukhari Ghani Hashmi Kazmi Lodhi Memon Mughal Nizami Quadri Rahmani Saifi
        Usmani Wani Dar Lone Mir Sayyed Inamdar Pinjari Mulla Attar Khwaja Chishti Ashraf Kidwai Rehman""",
    },
    "christian": {
        "M": """Joseph Thomas George Jacob Mathew Varghese Abraham Philip Samuel Daniel Joshua Peter Paul Xavier
        Francis Anthony Jose Sebastian Cyril Alwyn Clifford Denzil Eric Gerard Ivan Joel Lawrence Melvin Nelson
        Oswald Rodney Stanley Vincent Wilfred Aaron Benedict Carl Dominic Elijah Ferdinand Gilbert Jerome
        Leslie Neville Roshan Terence Valentine""",
        "F": """Mary Maria Anna Susan Elizabeth Sheela Jessy Blessy Rosy Merlin Jennifer Priscilla Angela
        Bernadette Carol Diana Eunice Flavia Gladys Hazel Irene Jacinta Leena Marina Noreen Olivia Pamela
        Queenie Rita Shirley Teresa Ursula Veronica Winifred Agnes Beena Celine Annie Cecilia Jemima Lilian
        Pauline Rosemary""",
        "L": """Fernandes Pereira D'Souza D'Cruz Rodrigues Gonsalves Menezes Lobo Pinto Coelho Mascarenhas Saldanha
        Vaz Dias Almeida Noronha Colaco Pais Kunnel Kallarackal Chacko Kuruvilla Puthenpurackal Mampilly
        Thekkekara Vadakkan Pulickal Panicker Nadar D'Silva Crasto Sequeira Monteiro Barretto Mendonca
        Alphonso Britto Cardozo Correia Gomes Miranda Rebello Viegas Xavier""",
    },
    "sikh": {
        "M": """Harpreet Gurpreet Jasvinder Manpreet Amarjeet Balwinder Charanjit Davinder Gurdeep Gurmeet
        Harjinder Inderjit Jaspal Kuldeep Lakhwinder Maninder Navdeep Paramjit Rajinder Sarabjit Tejinder
        Gagandeep Hardeep Jagjit Karamjit Satnam Sukhdev Sukhwinder Tarlochan Amritpal Arshdeep Bikramjit
        Gurinder Jaswant Mandeep Ravinder Simranjit Baljit Daljeet Gursharan Jatinder""",
        "F": """Harleen Gurleen Jasleen Kiranjot Manjeet Navneet Rupinder Simran Sukhpreet Amandeep Balbir
        Charanjeet Daljit Harpal Jaspreet Kamaljit Mehar Prabhleen Rajwinder Taranjeet Veerpal Amrit Gurvinder
        Inderpreet Jasmeet Kulwinder Paramjeet Ravneet""",
        "L": """Singh Gill Sidhu Dhillon Brar Grewal Sandhu Bajwa Randhawa Cheema Virk Bedi Kohli Ahluwalia Bhatia
        Chawla Arora Sethi Talwar Walia Sodhi Bhullar Mann Dhaliwal Atwal Kang Toor Johal Kaler Kaur Khalsa
        Lamba Narula Oberoi Puri Sabharwal Sachdeva Suri Uppal Wadhwa Anand Gulati""",
    },
    "parsi": {
        "M": """Cyrus Darius Jamshed Kaikhushru Rustom Sorab Zubin Burzin Firoz Behram Homi Jehangir Kersi Minoo
        Navroz Pesi Rohinton Xerxes Yazdi Boman Darab Erach Hoshang Jimmy Keki Nariman Phiroze Porus Ratan
        Soli Viraf Adil Farokh Godfrey Hormuz Kaizad Mehli Noshir Pheroze Sarosh Zarir""",
        "F": """Zenobia Roshan Dinaz Freny Gulnar Hutokshi Khorshed Mehrnavaz Naaz Perin Shernaz Tehmina Villoo
        Zarine Armaity Bapsy Dilnavaz Havovi Jehroo Mahrukh Niloufer Parizad Ruttie Shirin Aban Coomi Dolly
        Firuza Godavari Kashmira Meher Nergish Pervin Silloo""",
        "L": """Wadia Mistry Godrej Irani Cama Contractor Engineer Dastur Bharucha Colah Daruwala Kapadia Sethna
        Vakil Bilimoria Commissariat Panthaki Pavri Mody Dalal Nanavati Tarapore Screwvala Poonawalla Cooper
        Bhesania Gazdar Jokhi Sidhwa Doctor Unwalla Khambatta Banaji Marker Hathiram Avari Batliwala
        Billimoria Chinoy Deboo Elavia""",
    },
    "global": {
        "M": """James John Robert Michael William David Richard Charles Christopher Kenneth Kevin Brian Edward
        Ronald Timothy Jason Jeffrey Ryan Gary Nicholas Jonathan Stephen Larry Justin Scott Brandon Benjamin
        Gregory Alexander Raymond Patrick Dennis Jerry Tyler Henry Douglas Adam Nathan Zachary Kyle Walter
        Harold Carlos Juan Luis Pierre Hans Lars Sven Marco Mateo Luca Giovanni Dmitri Sergei Yuki Hiroshi
        Kenji Minjun Tomasz Piotr Andrzej Oliver Liam Noah Lucas Mason Ethan Logan Hugo Felix Emil Anders
        Stefan Klaus Jurgen Antoine Mikhail Takeshi Jinwoo Wei Jun""",
        "F": """Linda Patricia Barbara Jessica Sarah Karen Nancy Lisa Betty Margaret Sandra Ashley Kimberly Emily
        Donna Michelle Amanda Melissa Deborah Stephanie Rebecca Laura Sharon Cynthia Kathleen Amy Brenda Emma
        Nicole Samantha Katherine Christine Helen Debra Rachel Carolyn Janet Heather Diane Julie Joyce
        Victoria Kelly Christina Lauren Megan Andrea Cheryl Hannah Jacqueline Martha Gloria Sara Janice
        Julia Sophie Chloe Isabelle Ingrid Astrid Elena Sofia Giulia Anastasia Mei Hana Yuna Aiko Sakura
        Natasha Charlotte Amelia Harper Ava Mia Ines Marta Katarzyna Ewa Svetlana""",
        "L": """Smith Johnson Williams Jones Miller Davis Wilson Anderson Taylor Moore Jackson Martin Thompson
        Harris Clark Lewis Robinson Walker Allen Wright Torres Nguyen Flores Adams Nelson Baker Rivera Campbell
        Mitchell Carter Roberts Gomez Phillips Evans Turner Diaz Parker Cruz Edwards Collins Reyes Stewart
        Morris Morales Murphy Rogers Morgan Peterson Cooper Bailey Kelly Howard Ward Cox Richardson Watson
        Brooks Bennett Hughes Sanders Myers Foster Powell Jenkins Perry Russell Sullivan Fisher Henderson
        Hamilton Graham Wallace Ellis Harrison Gibson Marshall Ortiz Murray Freeman Wells Webb Simpson Stevens
        Tucker Porter Hunter Hicks Crawford Boyd Kennedy Warren Dixon Burns Gordon Holmes Robertson Daniels
        Palmer Nichols Knight Ferguson Hawkins Dunn Perkins Hudson Spencer Gardner Payne Pierce Matthews
        Arnold Wagner Willis Watkins Olson Carroll Duncan Snyder Cunningham Bradley Andrews Ruiz Harper
        Armstrong Carpenter Weaver Greene Lawrence Elliott Chavez Austin Peters Franklin Lawson Muller
        Schmidt Schneider Fischer Weber Meyer Becker Hoffmann Rossi Russo Ferrari Esposito Bianchi Romano
        Colombo Ricci Dubois Laurent Lefevre Moreau Garcia Martinez Rodriguez Lopez Hernandez Gonzalez Perez
        Sanchez Ramirez Silva Santos Oliveira Souza Kowalski Nowak Ivanov Petrov Sato Suzuki Takahashi Tanaka
        Watanabe Yamamoto Nakamura Kim Park Choi Wang Zhang Liu Chen Yang Huang Zhao Zhou Hansen Nielsen
        Andersson Johansson Larsen Olsen Novak Horvat Popescu Dimitrov""",
    },
}
REGION_WEIGHTS = {"north": 22, "south": 20, "east": 10, "west": 14, "northeast": 4, "muslim": 8,
                  "christian": 7, "sikh": 5, "parsi": 2, "global": 8}
# Words that must never become a person name token because they are ordinary words in the corpus.
_NAME_BLOCKLIST = {"kumar_"}


def _tokens(s: str) -> list[str]:
    return [w for w in s.split() if len(w) >= 3]


def _alpha_tokens(w: str) -> set[str]:
    """Alphabetic sub-tokens (D'Souza -> {souza}); disjointness is enforced on these."""
    return {m.lower() for m in re.findall(r"[^\W\d_]+", w) if len(m) > 1}


def build_name_tables():
    """-> (first[(region, gender)] -> [names], last[region] -> [names]); no alphabetic token is shared
    between any two names in different lists or roles."""
    seen: set[str] = set()
    first: dict = {}

    def take(w):
        toks = _alpha_tokens(w)
        if not toks or toks & seen:
            return False
        seen.update(toks)
        return True

    for region in sorted(NAMES):
        for gdr in "MF":
            first[(region, gdr)] = sorted(w for w in _tokens(NAMES[region][gdr]) if take(w))
    last: dict = {}
    for region in sorted(NAMES):
        last[region] = sorted(w for w in _tokens(NAMES[region]["L"]) if take(w))
    return first, last


def _partition(lst, rng):
    """60/20/20 disjoint partition (dev, tune, test), each non-empty."""
    l = list(lst)
    rng.shuffle(l)
    n = len(l)
    a = max(1, round(n * 0.6))
    b = max(1, round(n * 0.2))
    if a + b >= n:
        a, b = max(1, n - 2), 1
    return {"dev": l[:a], "tune": l[a:a + b], "test": l[a + b:]}


class NamePool:
    """Name tokens available to one split.  roster_* and ext_* are disjoint token sets."""

    def __init__(self):
        self.first = {"roster": {}, "external": {}}   # source -> (region, gender) -> list
        self.last = {"roster": {}, "external": {}}    # source -> region -> list

    def draw(self, rng, source):
        """-> (first, last, gender, region) using only this pool's tokens for `source`."""
        regions = sorted(REGION_WEIGHTS)
        region = rng.choices(regions, [REGION_WEIGHTS[r] for r in regions])[0]
        gdr = rng.choice("MF")
        f = self.first[source][(region, gdr)]
        l = self.last[source][region]
        return rng.choice(f), rng.choice(l), gdr, region


def build_name_pools(seed):
    r = random.Random(f"{seed}|names")
    first, last = build_name_tables()
    pools = {s: NamePool() for s in SPLITS}
    for key in sorted(first):
        lst = list(first[key])
        r.shuffle(lst)
        k = round(len(lst) * 0.7)
        for source, part in (("roster", lst[:k]), ("external", lst[k:])):
            for s, names in _partition(part, r).items():
                pools[s].first[source][key] = names
    for region in sorted(last):
        lst = list(last[region])
        r.shuffle(lst)
        k = round(len(lst) * 0.7)
        for source, part in (("roster", lst[:k]), ("external", lst[k:])):
            for s, names in _partition(part, r).items():
                pools[s].last[source][region] = names
    return pools


# --------------------------------------------------------------------------- people
class Person:
    __slots__ = ("first", "last", "gender", "region", "source", "email", "phone10", "emp_id", "mono")

    def __init__(self, first, last, gender, region, source, email, phone10, emp_id=None, mono=False):
        self.first, self.last, self.gender, self.region = first, last, gender, region
        self.source, self.email, self.phone10, self.emp_id, self.mono = source, email, phone10, emp_id, mono

    @property
    def full(self):
        return self.first if self.mono or not self.last else f"{self.first} {self.last}"


def _slug(s):
    return "".join(c for c in s.lower() if c.isalpha())


def _rand_mobile(rng):
    d0 = rng.choices("6789", [10, 25, 25, 40])[0]
    return d0 + "".join(rng.choice("0123456789") for _ in range(9))


def fmt_roster_phone(p10):
    return f"+91 {p10[:5]} {p10[5:]}"


def build_roster(seed, pools):
    """Tenant directory ~1,500 employees; names only from the split's roster pools."""
    r = random.Random(f"{seed}|roster")
    counts = {"dev": 900, "tune": 300, "test": 300}
    people = {s: [] for s in SPLITS}
    used_names, used_email, used_phone = set(), set(), set()
    ids = r.sample(range(100, 20000), sum(counts.values()))
    idx = 0
    for s in SPLITS:
        while len(people[s]) < counts[s]:
            f, l, g, reg = pools[s].draw(r, "roster")
            if (f, l) in used_names:
                continue
            used_names.add((f, l))
            local = f"{_slug(f)}.{_slug(l)}"
            email = f"{local}@{TENANT_DOMAIN}"
            n = 2
            while email in used_email:
                email = f"{local}{n}@{TENANT_DOMAIN}"
                n += 1
            used_email.add(email)
            ph = _rand_mobile(r)
            while ph in used_phone:
                ph = _rand_mobile(r)
            used_phone.add(ph)
            people[s].append(Person(f, l, g, reg, "roster", email, ph, f"EMP-{ids[idx]:05d}"))
            idx += 1
    return people


def roster_rows(people):
    rows = []
    for s in SPLITS:
        for p in people[s]:
            rows.append({"employee_id": p.emp_id, "full_name": p.full, "first_name": p.first,
                         "last_name": p.last, "email": p.email, "phone": fmt_roster_phone(p.phone10)})
    rows.sort(key=lambda x: x["employee_id"])
    return rows


# --------------------------------------------------------------------------- checksums
_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
      [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
      [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
      [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
      [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
      [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_check_digit(payload: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(payload)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[c])


def verhoeff_valid(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def luhn_check_digit(payload: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(payload)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def luhn_valid(num: str) -> bool:
    return luhn_check_digit(num[:-1]) == num[-1]


def inr_group(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail])


def west_group(n: int) -> str:
    return f"{n:,}"


# --------------------------------------------------------------------------- builder
class E:
    """A sensitive value to be appended (gold span)."""
    __slots__ = ("v", "t", "src")

    def __init__(self, v, t, src=None):
        self.v, self.t, self.src = v, t, src


class Builder:
    def __init__(self):
        self.parts: list[str] = []
        self.n = 0
        self.spans: list[tuple[int, int, str, str]] = []
        self.srcs: list[str] = []

    def __call__(self, *pieces):
        return self.add(*pieces)

    def add(self, *pieces):
        for p in pieces:
            if p is None:
                continue
            if isinstance(p, list):
                self.add(*p)
            elif isinstance(p, E):
                assert p.v and p.v.strip(), "empty/whitespace entity"
                self.spans.append((self.n, self.n + len(p.v), p.t, p.v))
                self.parts.append(p.v)
                self.n += len(p.v)
                if p.t == "PERSON":
                    self.srcs.append(p.src)
            else:
                s = str(p)
                self.parts.append(s)
                self.n += len(s)
        return self

    @property
    def text(self):
        return "".join(self.parts)

    def add_mapped(self, inner: "Builder", esc):
        """Append inner's text through per-character escape function `esc(ch)->str`,
        carrying spans across via an offset map (still construction, never search)."""
        text = inner.text
        starts = []
        out = []
        pos = self.n
        for ch in text:
            starts.append(pos)
            e = esc(ch)
            out.append(e)
            pos += len(e)
        starts.append(pos)
        for s, e, t, v in inner.spans:
            new_s, new_e = starts[s], starts[e]
            self.spans.append((new_s, new_e, t, "".join(out)[new_s - self.n:new_e - self.n]))
        self.srcs.extend(inner.srcs)
        self.parts.append("".join(out))
        self.n = pos
        return self

    def finish(self):
        text = self.text
        spans = sorted(self.spans)
        prev = 0
        for s, e, t, v in spans:
            assert 0 <= s < e <= len(text) and s >= prev, "bad/overlapping span"
            assert text[s:e] == v, "span text mismatch"
            prev = e
        return text, [{"start": s, "end": e, "type": t} for s, e, t, v in spans]


def jdump(b: Builder, obj, indent=2, level=0):
    """Serialise obj (dict/list/scalars/E) into builder, tracking spans.  indent=None -> compact."""
    def nl(lv):
        return "" if indent is None else "\n" + " " * (indent * lv)
    if isinstance(obj, dict):
        if not obj:
            b("{}")
            return
        b("{")
        for i, (k, v) in enumerate(obj.items()):
            b(nl(level + 1), json.dumps(k, ensure_ascii=False), ": " if indent is not None else ":")
            jdump(b, v, indent, level + 1)
            if i < len(obj) - 1:
                b(",")
        b(nl(level), "}")
    elif isinstance(obj, list):
        if not obj:
            b("[]")
            return
        b("[")
        for i, v in enumerate(obj):
            b(nl(level + 1))
            jdump(b, v, indent, level + 1)
            if i < len(obj) - 1:
                b(",")
        b(nl(level), "]")
    elif isinstance(obj, E):
        if getattr(obj, "src", None) == "__num__":
            b(obj)                       # bare JSON number
        else:
            assert json.dumps(obj.v, ensure_ascii=False)[1:-1] == obj.v, "value needs JSON escaping"
            b('"', obj, '"')
    else:
        b(json.dumps(obj, ensure_ascii=False))


def num(v, t):
    """Entity emitted as a bare JSON number."""
    return E(v, t, "__num__")


def json_esc(ch):
    return {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\t": "\\t", "\r": "\\r"}.get(ch, ch)


# --------------------------------------------------------------------------- generation context
_DIG = string.digits
_ALNUM = string.ascii_letters + string.digits
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December"]
HON = {"M": ["Mr.", "Mr.", "Mr.", "Shri", "Dr.", "Sri"], "F": ["Ms.", "Ms.", "Mrs.", "Smt.", "Dr.", "Ms."]}
SUFFIX = {"M": ["ji", "bhai", "sir", "sahab", "sir"], "F": ["ji", "madam", "ma'am", "didi", "madam"]}
PERSONAL_DOMAINS = ["gmail.com", "gmail.com", "gmail.com", "yahoo.co.in", "yahoo.com", "outlook.com",
                    "rediffmail.com", "hotmail.com", "protonmail.com", "icloud.com"]
UPI_PROVIDERS = ["okhdfcbank", "okicici", "oksbi", "okaxis", "ybl", "ibl", "axl", "paytm", "apl", "upi"]
IFSC_BANKS = ["HDFC", "ICIC", "SBIN", "UTIB", "KKBK", "PUNB", "BARB", "YESB", "IDIB", "CNRB", "UBIN",
              "IDFB", "INDB", "FDRL", "BKID"]
PW_WORDS = ["Summer", "Winter", "Welcome", "Payroll", "Sunshine", "Monsoon", "Qwerty", "Admin", "Kestrel",
            "Hrms", "Workday", "Mumbai", "Chennai", "Falcon", "Tiger", "Orange", "Passw0rd", "Login"]
DATE_FMTS = ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%B %d, %Y", "%d.%m.%Y", "%d-%b-%Y"]


def fmt_date(d: date, style=None, rng=None):
    if style is None:
        style = rng.choices(range(9), [22, 8, 16, 14, 8, 6, 8, 6, 4])[0]
    if style == 8:
        n = d.day
        suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return f"{n}{suf} {_MONTHS[d.month - 1]} {d.year}"
    return d.strftime(DATE_FMTS[style])


class Gen:
    def __init__(self, rng, split, roster, pool, adj=0.0):
        self.rng, self.split, self.roster, self.pool = rng, split, roster, pool
        self.adj = adj          # feedback so ~70 % of person mentions in a split come from the roster
        self.b = Builder()
        self.cnt: Counter = Counter()
        self.cache: dict = {}
        self._keep: list = []   # keep Person objects alive so id()-keyed caches can never collide

    # ---- people
    def roster_person(self, exclude=()):
        for _ in range(50):
            p = self.rng.choice(self.roster)
            if p not in exclude:
                return p
        return p

    def ext_person(self, mono=None):
        r = self.rng
        f, l, gdr, reg = self.pool.draw(r, "external")
        if mono is None:
            mono = r.random() < 0.08
        d = r.choice(PERSONAL_DOMAINS)
        s = r.randrange(6)
        sf, sl = _slug(f), _slug(l)
        local = [f"{sf}.{sl}", f"{sf}_{sl}", f"{sf}{r.randint(1, 99)}", f"{sf[0]}{sl}", f"{sl}.{sf}",
                 f"{sf}{sl}{r.randint(70, 99)}"][s]
        if mono:
            local = f"{sf}{r.randint(10, 999)}"
        return Person(f, l, gdr, reg, "external", f"{local}@{d}", _rand_mobile(r), None, mono)

    def subject(self, p_roster=0.76, exclude=()):
        if self.rng.random() < min(1.0, max(0.0, p_roster + self.adj)):
            return self.roster_person(exclude)
        return self.ext_person()

    def other(self, p_roster=0.93, exclude=()):
        return self.subject(p_roster, exclude)

    def pr(self, p):
        return ("he", "his", "him") if p.gender == "M" else ("she", "her", "her")

    def ref(self, p, style=None, casual=False):
        r = self.rng
        self._keep.append(p)
        n = self.cnt[id(p)]
        self.cnt[id(p)] += 1
        if style is None:
            if n == 0:
                opts = [("full", 52), ("hon_full", 14), ("init", 9), ("first", 13),
                        ("lower_full", 8 if casual else 1), ("last_first", 3)]
            else:
                opts = [("first", 42), ("hon_last", 16), ("full", 8), ("init", 9), ("first_sfx", 10),
                        ("lower_first", 10 if casual else 2), ("last", 5)]
            style = r.choices([o[0] for o in opts], [o[1] for o in opts])[0]
        src = p.source
        first, last = p.first, (p.last or p.first)
        if p.mono and style in ("init", "last_first", "last"):
            style = "first"
        hon = r.choice(HON[p.gender])
        if style == "full":
            return [E(p.full, "PERSON", src)]
        if style == "first":
            return [E(first, "PERSON", src)]
        if style == "hon_full":
            return [hon + " ", E(p.full, "PERSON", src)]
        if style == "hon_last":
            return [hon + " ", E(last, "PERSON", src)]
        if style == "init":
            return [E(f"{first[0]}. {last}", "PERSON", src)]
        if style == "lower_full":
            return [E(p.full.lower(), "PERSON", src)]
        if style == "lower_first":
            return [E(first.lower(), "PERSON", src)]
        if style == "upper_full":
            return [E(p.full.upper(), "PERSON", src)]
        if style == "last_first":
            return [E(f"{last}, {first}", "PERSON", src)]
        if style == "last":
            return [E(last, "PERSON", src)]
        if style == "first_sfx":
            return [E(first, "PERSON", src), " " + r.choice(SUFFIX[p.gender])]
        if style == "last_sfx":
            return [E(last, "PERSON", src), " " + r.choice(SUFFIX[p.gender])]
        raise ValueError(style)

    def email(self, p):
        return E(p.email, "EMAIL")

    def mailbox(self):
        loc = self.rng.choice(["hr-helpdesk", "payroll", "hrops", "people.team", "benefits", "talent.acq", "it-support"])
        return E(f"{loc}@{TENANT_DOMAIN}", "EMAIL")

    # ---- values
    def _digits(self, n):
        return "".join(self.rng.choice(_DIG) for _ in range(n))

    def phone(self, p=None):
        r = self.rng
        d = p.phone10 if p is not None else _rand_mobile(r)
        if r.random() < 0.05:
            std = r.choice(["022", "080", "011", "040", "044", "020", "033"])
            return E(f"{std}-{self._digits(4)} {self._digits(4)}", "PHONE")
        k = r.choices(range(10), [18, 15, 20, 9, 10, 7, 5, 5, 4, 7])[0]
        v = [d, f"{d[:5]} {d[5:]}", f"+91 {d[:5]} {d[5:]}", f"+91-{d}", f"+91{d}", f"+91-{d[:5]}-{d[5:]}",
             f"0{d}", f"(+91) {d[:5]}-{d[5:]}", f"91 {d}", f"+91 {d[:3]} {d[3:6]} {d[6:]}"][k]
        return E(v, "PHONE")

    def aadhaar(self):
        r = self.rng
        base = r.choice("23456789") + self._digits(10)
        n = base + verhoeff_check_digit(base)
        assert verhoeff_valid(n)
        k = r.choices(range(3), [65, 25, 10])[0]
        return E([f"{n[:4]} {n[4:8]} {n[8:]}", n, f"{n[:4]}-{n[4:8]}-{n[8:]}"][k], "AADHAAR")

    def pan(self, p=None, lower=0.0):
        r = self.rng
        L = string.ascii_uppercase
        ini = (p.last or p.first)[0].upper() if p is not None else r.choice(L)
        v = "".join(r.choice(L) for _ in range(3)) + "P" + ini + self._digits(4) + r.choice(L)
        if lower and r.random() < lower:
            v = v.lower()
        return E(v, "PAN")

    def card(self):
        r = self.rng
        kind = r.choices(["visa", "mc", "amex", "rupay"], [45, 30, 10, 15])[0]
        if kind == "visa":
            body = "4" + self._digits(14)
        elif kind == "mc":
            body = r.choice(["51", "52", "53", "54", "55"]) + self._digits(13)
        elif kind == "amex":
            body = r.choice(["34", "37"]) + self._digits(12)
        else:
            body = r.choice(["60", "65", "81", "82"]) + self._digits(13)
        n = body + luhn_check_digit(body)
        assert luhn_valid(n)
        k = r.choices(range(3), [60, 22, 18])[0]
        if kind == "amex":
            v = [f"{n[:4]} {n[4:10]} {n[10:]}", n, f"{n[:4]}-{n[4:10]}-{n[10:]}"][k]
        else:
            v = [" ".join(n[i:i + 4] for i in range(0, 16, 4)), n, "-".join(n[i:i + 4] for i in range(0, 16, 4))][k]
        return E(v, "CARD")

    def acct(self):
        r = self.rng
        n = r.choices([9, 10, 11, 12, 13, 14, 15, 16, 17, 18], [3, 6, 14, 12, 8, 20, 12, 12, 5, 8])[0]
        return E(r.choice("123456789") + self._digits(n - 1), "BANK_ACCOUNT")

    def ifsc(self):
        r = self.rng
        tail = "".join(r.choice(_DIG) if r.random() < 0.8 else r.choice(string.ascii_uppercase) for _ in range(6))
        return E(r.choice(IFSC_BANKS) + "0" + tail, "IFSC")

    def upi(self, p=None):
        r = self.rng
        if p is None:
            p = self.ext_person()
        f, l = _slug(p.first), _slug(p.last or p.first)
        h = [p.phone10, f"{f}{l}", f"{f}.{l}", f"{f}{r.randint(1, 99)}", f"{f[0]}{l}{r.randint(1, 999)}",
             f"{f}-{r.randint(1, 9)}", f"{l}{f[0]}{r.randint(80, 99)}"][r.choices(range(7), [22, 16, 16, 16, 12, 8, 10])[0]]
        return E(f"{h}@{r.choice(UPI_PROVIDERS)}", "UPI_ID")

    def dob_date(self, p):
        self._keep.append(p)
        key = ("dob", id(p))
        if key not in self.cache:
            self.cache[key] = date(1968, 1, 1) + timedelta(days=self.rng.randint(0, 35 * 365))
        return self.cache[key]

    def dob(self, p, style=None):
        return E(fmt_date(self.dob_date(p), style, self.rng), "DATE_OF_BIRTH")

    def dob_str_iso(self, p):
        return E(self.dob_date(p).isoformat(), "DATE_OF_BIRTH")

    def comp_seq(self, n=2):
        """n increasing annual amounts (e.g. current CTC then revised/offer)."""
        r = self.rng
        x = r.randint(30, 700) / 10
        out = []
        for _ in range(n):
            out.append(self.comp(annual_lakh=x))
            x = round(x * r.uniform(1.06, 1.3), 1)
        return out

    def date_range(self, iso=False, style=None):
        r = self.rng
        d1 = self.rand_date()
        d2 = d1 + timedelta(days=r.randint(0, 12))
        if iso:
            return d1.isoformat(), d2.isoformat()
        st = r.randrange(9) if style is None else style
        return fmt_date(d1, st, r), fmt_date(d2, st, r)

    def rand_date(self, y0=2023, y1=2026):
        d0 = date(y0, 1, 1)
        return d0 + timedelta(days=self.rng.randint(0, (date(y1, 12, 31) - d0).days))

    def date_txt(self, style=None):
        return fmt_date(self.rand_date(), style, self.rng)

    def empid(self, p=None, fmt=None):
        r = self.rng
        if fmt is None:
            if p is not None and p.emp_id and r.random() < 0.75:
                return E(p.emp_id, "EMPLOYEE_ID")
            fmt = r.choices(["EMP", "E", "WD"], [30, 40, 30])[0]
        if fmt == "EMP":
            v = f"EMP-{r.randint(1, 99999):05d}"
        elif fmt == "E":
            v = f"E{r.randint(100000, 999999)}"
        else:
            v = f"WD-{r.randint(1000000, 9999999)}"
        return E(v, "EMPLOYEE_ID")

    def comp(self, monthly=False, plain=False, annual_lakh=None, western=False):
        r = self.rng
        if western:
            amt = r.randrange(40000, 260000, 500)
            v = r.choice([f"USD {west_group(amt)}", f"${west_group(amt)}", f"£{west_group(int(amt * 0.55))}",
                          f"EUR {west_group(int(amt * 0.85))}", f"USD {west_group(amt)}"])
            return E(v, "COMPENSATION")
        if monthly:
            amt = r.randrange(20000, 400000, 500)
            v = r.choice([f"₹{inr_group(amt)}", f"Rs. {inr_group(amt)}", f"INR {inr_group(amt)}",
                          f"Rs {inr_group(amt)}", f"₹ {inr_group(amt)}"])
            return E(v, "COMPENSATION")
        x = annual_lakh if annual_lakh is not None else r.randint(30, 900) / 10
        annual = round(x * 100000)
        xs = f"{x:g}"
        if plain:
            return E(r.choice([str(annual), inr_group(annual)]), "COMPENSATION")
        k = r.choices(range(12), [16, 10, 6, 9, 10, 10, 8, 5, 5, 3, 4, 4])[0]
        v = [f"₹{inr_group(annual)}", f"Rs. {inr_group(annual)}", f"Rs {inr_group(annual)}",
             f"INR {inr_group(annual)}", f"INR {xs} lakh", f"{xs} LPA", f"{xs}L", f"₹{xs} lakhs",
             f"Rs. {xs} Lakhs", f"₹ {inr_group(annual)}", f"INR {xs} lakhs", f"{xs} lpa"][k]
        return E(v, "COMPENSATION")

    # ---- secrets
    def _rand(self, alphabet, n):
        return "".join(self.rng.choice(alphabet) for _ in range(n))

    def secret(self, kind=None):
        r = self.rng
        kind = kind or r.choice(["sk", "akia", "ghp", "jwt", "bearer", "password", "awssecret", "sk_proj"])
        if kind == "sk":
            v = "sk-" + self._rand(_ALNUM, 48)
        elif kind == "sk_proj":
            v = "sk-proj-" + self._rand(_ALNUM + "_-", 64)
        elif kind == "akia":
            v = "AKIA" + self._rand(string.ascii_uppercase + _DIG, 16)
        elif kind == "ghp":
            v = "ghp_" + self._rand(_ALNUM, 36)
        elif kind == "awssecret":
            v = self._rand(_ALNUM + "/+", 40)
        elif kind == "jwt":
            v = self.jwt()
        elif kind == "bearer":
            v = self._rand(_ALNUM + "-_", r.choice([32, 40, 43, 64]))
        else:
            v = self.password()
        return E(v, "SECRET")

    def password(self):
        r = self.rng
        w = r.choice(PW_WORDS)
        return r.choice([f"{w}{r.choice('@#!$%*')}{r.randint(10, 9999)}", f"{w}{r.randint(1, 99)}{r.choice('!@#')}{w[:3].lower()}",
                         self._rand(_ALNUM, r.randint(10, 18)), f"{w.lower()}_{self._rand(_DIG, 4)}{r.choice('!#*')}"])

    def jwt(self, claims=None):
        r = self.rng
        hdr = {"alg": r.choice(["HS256", "RS256"]), "typ": "JWT"}
        now = 1700000000 + r.randint(0, 90000000)
        pl = claims or {"sub": self._rand(_DIG, 8), "iss": "https://auth.kestrelsys.in/", "iat": now, "exp": now + 3600,
                        "scope": r.choice(["hr.read", "hr.read hr.write", "payroll.read"])}
        enc = lambda o: base64.urlsafe_b64encode(json.dumps(o, separators=(",", ":")).encode()).decode().rstrip("=")
        sig = base64.urlsafe_b64encode(bytes(r.getrandbits(8) for _ in range(32))).decode().rstrip("=")
        return f"{enc(hdr)}.{enc(pl)}.{sig}"

    # ---- non-sensitive look-alikes
    def order12(self):
        """Random 12-digit order id: most fail Verhoeff, ~12 % pass (as random ids would)."""
        r = self.rng
        base = r.choice("23456789") + self._digits(10)
        if r.random() < 0.12:
            return base + verhoeff_check_digit(base)
        n = base + self._digits(1)
        return n

    def uuid(self):
        h = self._rand("0123456789abcdef", 32)
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"

    def hexs(self, n):
        return self._rand("0123456789abcdef", n)

    def ip(self):
        r = self.rng
        return r.choice([f"10.{r.randint(0, 255)}.{r.randint(0, 255)}.{r.randint(1, 254)}",
                         f"172.{r.randint(16, 31)}.{r.randint(0, 255)}.{r.randint(1, 254)}",
                         f"192.168.{r.randint(0, 255)}.{r.randint(1, 254)}",
                         f"{r.randint(13, 223)}.{r.randint(0, 255)}.{r.randint(0, 255)}.{r.randint(1, 254)}"])

    def finish(self):
        text, spans = self.b.finish()
        srcs = set(s for s in self.b.srcs if s)
        ns = "none" if not srcs else (next(iter(srcs)) if len(srcs) == 1 else "mixed")
        return text, spans, ns, list(self.b.srcs)


TEMPLATES: list = []   # (slice, template_id, fn)


def tpl(slice_, tid):
    def deco(fn):
        TEMPLATES.append((slice_, tid, fn))
        return fn
    return deco


PROJECTS = ["the Q2 payroll migration", "the vendor onboarding workstream", "the SAP cutover", "the Workday tenant refresh",
            "the appraisal calibration", "the ISO audit prep", "the new-joiner induction", "the month-end close",
            "the Pune office move", "the benefits renewal"]
CONCERNS = ["being excluded from planning calls", "repeated last-minute changes to shift rosters",
            "a public comment made in the team channel", "unclear ownership on the reconciliation task",
            "feedback that was shared without a private conversation first", "comp-off not being credited",
            "being asked to work on a weekend without notice", "an unfair rating in the mid-year cycle"]

# --------------------------------------------------------------------------- HR free text
@tpl("hr_free_text", "hr.case_note")
def t_case_note(g):
    b, r = g.b, g.rng
    emp, mgr = g.subject(), g.other()
    he, his, him = g.pr(emp)
    b(f"Case ER-{r.randint(1000, 9999)} | Opened {g.date_txt()} | Status: {r.choice(['Open', 'In review', 'Awaiting response', 'Escalated'])}\n")
    b("Employee: ", g.ref(emp, "full"), " (", g.empid(emp), ")\n")
    b("Reporting manager: ", g.ref(mgr, "full"), "\n\nSummary: ")
    k = r.randrange(4)
    if k == 0:
        b(g.ref(emp), f" raised a concern about {r.choice(CONCERNS)} on {r.choice(PROJECTS)}. ", g.ref(mgr, "hon_last"),
          f" says this was a misunderstanding. HRBP to speak with ", g.ref(emp), f" by {g.date_txt()} and revert.")
    elif k == 1:
        b("Met ", g.ref(emp), f" at 3pm. {he.capitalize()} was calm but upset about {r.choice(CONCERNS)}. ",
          f"{his.capitalize()} manager ", g.ref(mgr), f" has not been informed yet. Next step: informal resolution, follow-up call on {g.date_txt()}. ",
          "Contact for follow-up: ", g.phone(emp), ".")
    elif k == 2:
        b("Complaint received via email from ", g.email(emp), ". ", g.ref(emp, "hon_full"), f" alleges {r.choice(CONCERNS)}. ",
          "No witnesses named. ", g.ref(mgr, "init"), " to be interviewed separately. Do not share with the team.")
    else:
        b(g.ref(emp), " ke saath baat hui, ", he, f" bol raha hai ki {r.choice(CONCERNS)} ka issue hai. ", g.ref(mgr),
          " ko abhi nahi bataya hai. Agle hafte follow-up karna hai, ", g.ref(emp), " ka number ", g.phone(emp), " hai.")
    b("\n")


@tpl("hr_free_text", "hr.grievance")
def t_grievance(g):
    b, r = g.b, g.rng
    emp, mgr, wit = g.subject(), g.other(), g.other()
    he, his, him = g.pr(emp)
    b("GRIEVANCE SUMMARY\n")
    b("Raised by: ", g.ref(emp, "hon_full"), "\nAgainst: ", g.ref(mgr, "full"), "\nDate received: ", g.date_txt(), "\n\n")
    b("Nature of grievance: ", r.choice(CONCERNS), ". ", g.ref(emp, "first"), f" states that {r.choice(PROJECTS)} was the trigger. ")
    b("Witness named: ", g.ref(wit, "init"), ". ")
    b(g.ref(wit, "hon_last"), " confirmed ", "part of the account when contacted on ", g.phone(wit), ". ")
    b("\nRecommended action: mediation between ", g.ref(emp, "last_first"), " and ", g.ref(mgr, "first"), f". Grievance officer sign-off pending.\n")


@tpl("hr_free_text", "hr.appraisal")
def t_appraisal(g):
    b, r = g.b, g.rng
    emp = g.subject()
    mgr = g.other()
    he, his, him = g.pr(emp)
    rating = r.choice(["Exceeds Expectations", "Meets Expectations", "Partially Meets", "4/5", "3.5 / 5", "Outstanding"])
    b("Annual appraisal - manager comments\nEmployee: ", g.ref(emp, "full"), " | ID ", g.empid(emp), "\nRating: ", rating, "\n\n")
    k = r.randrange(3)
    cur, new = g.comp_seq(2)
    if k == 0:
        b(g.ref(emp), f" consistently delivered on {r.choice(PROJECTS)}. ", he.capitalize(),
          " takes ownership and mentors juniors. Needs to improve stakeholder communication. ",
          "Recommended increment: 12%, revised fixed pay ", new, ". Promotion case for next cycle. - ", g.ref(mgr, "full"))
    elif k == 1:
        b(g.ref(emp, "hon_last"), f" has been a steady performer this year. Missed two deadlines around {r.choice(PROJECTS)} but recovered well. ",
          "Proposed hike is in line with band; current CTC ", cur, " moving to ", new, ". Regards, ", g.ref(mgr))
    else:
        b(g.ref(emp), " ne is saal bahut accha kaam kiya. Client feedback positive tha. ", g.ref(emp),
          " ki salary review honi chahiye, abhi ", cur, " hai aur market ke hisaab se kam hai. ", g.ref(mgr, "init"), " ko approve karna hai.")
    b("\n")


@tpl("hr_free_text", "hr.email_hrbp_manager")
def t_email_hrbp_mgr(g):
    b, r = g.b, g.rng
    hrbp, mgr, emp = g.other(1.0), g.other(0.9), g.subject()
    b("From: ", g.ref(hrbp, "full"), " <", g.email(hrbp), ">\nTo: ", g.ref(mgr, "full"), " <", g.email(mgr), ">\n")
    b(f"Subject: {r.choice(['Re: ', 'FW: ', ''])}{r.choice(['Retention risk', 'Counter-offer discussion', 'Team restructuring', 'Notice period buyout'])} - ", g.ref(emp, "init"), "\n\n")
    b("Hi ", g.ref(mgr, "first"), ",\n\n")
    b(r.choice(["Following up on our call. ", "As discussed yesterday, ", "Quick note before the review meeting: "]))
    b(g.ref(emp, "full"), " has received an external offer of ")
    cur, off = g.comp_seq(2)
    b(off, ". Current CTC is ", cur, f". Given {r.choice(PROJECTS)} I would suggest we match. ")
    b(g.ref(emp), " is expecting a response by ", g.date_txt(), ". Please keep this confidential.\n\nRegards,\n", g.ref(hrbp, "full"), "\nHR Business Partner | ", g.phone(hrbp), "\n")


@tpl("hr_free_text", "hr.email_bank_update")
def t_email_bank(g):
    b, r = g.b, g.rng
    emp = g.subject(0.7)
    b("Subject: ", r.choice(["Bank account change request", "Update salary account", "Salary not credited - new account details"]), "\n\n")
    b("Hi Payroll team,\n\n")
    k = r.randrange(3)
    if k == 0:
        b("Please update my salary account. New details:\nAccount holder: ", g.ref(emp, "full"), "\nA/c No: ", g.acct(),
          "\nIFSC: ", g.ifsc(), "\nEmployee ID: ", g.empid(emp), "\nPAN: ", g.pan(emp), "\n")
    elif k == 1:
        b("My old account is closed. Kindly credit salary to account number ", g.acct(), " (IFSC ", g.ifsc(),
          "). I have attached the cancelled cheque. My PAN is ", g.pan(emp), " and employee code is ", g.empid(emp), ".\n")
    else:
        b("hi team, salary abhi tak nahi aayi. mera naya account hai ", g.acct(), ", ifsc ", g.ifsc(),
          ". UPI bhi update kar do - ", g.upi(emp), ". call me on ", g.phone(emp), " if any issue\n")
    b("\nThanks,\n", g.ref(emp, "full"), "\n")


@tpl("hr_free_text", "hr.leave_request")
def t_leave(g):
    b, r = g.b, g.rng
    emp, mgr = g.subject(0.7), g.other()
    k = r.randrange(4)
    d1, d2 = g.date_range()
    if k == 0:
        b("Hi ", g.ref(mgr, "first"), ",\n\nI would like to apply for ", r.choice(["5", "3", "10", "2"]), " days of leave from ", d1, " to ", d2,
          ". ", r.choice(["My father is not well and I need to travel home.", "It is for my sister's wedding.", "I have a medical procedure scheduled.", "Family function in hometown."]),
          " I will be reachable on ", g.phone(emp), ". Handover done to the team.\n\nRegards,\n", g.ref(emp, "full"), "\n")
    elif k == 1:
        b("Leave request | ", g.empid(emp), " | ", g.ref(emp, "full"), "\nType: ", r.choice(["Earned Leave", "Sick Leave", "Casual Leave", "Comp Off"]),
          "\nFrom: ", d1, "  To: ", d2, "\nApprover: ", g.ref(mgr, "full"), "\nReason: ", r.choice(["personal", "fever", "family emergency"]), "\n")
    elif k == 2:
        b(g.ref(mgr, "first_sfx"), " namaste, mujhe ", r.choice(["kal", "Monday", "Friday"]), " se ", r.choice(["3", "4", "2"]),
          " din ki chhutti chahiye, ghar mein kuch emergency hai. Phone pe available rahunga - ", g.phone(emp), ". ", g.ref(emp), "\n")
    else:
        b("Dear ", g.ref(mgr, "hon_last"), ",\nPlease approve my leave for ", g.date_txt(), ". ", g.ref(emp, "full"), " (", g.empid(emp),
          ") - handover to ", g.ref(g.other(), "first"), ".\n")


@tpl("hr_free_text", "hr.salary_letter")
def t_salary_letter(g):
    b, r = g.b, g.rng
    emp, hrh = g.subject(0.7), g.other(1.0)
    b(f"Date: {g.date_txt()}\n\nStrictly Private & Confidential\n\n", g.ref(emp, "hon_full"), "\nEmployee ID: ", g.empid(emp), "\n\n")
    b("Subject: Revision in compensation\n\nDear ", g.ref(emp, "hon_last"), ",\n\n")
    k = r.randrange(3)
    old, new = g.comp_seq(2)
    if k == 0:
        b(f"We are pleased to inform you that, based on your performance in the review cycle, your annual CTC has been revised from ", old, " to ", new,
          f" with effect from {g.date_txt()}. All other terms of your employment remain unchanged.\n")
    elif k == 1:
        b("Your revised fixed compensation will be ", new, " per annum, and your variable pay target is ", g.comp(), f". The revision is effective {g.date_txt()}. ",
          "Your monthly gross will be ", g.comp(monthly=True), " per month.\n")
    else:
        b("Congratulations on your promotion. Your new gross salary is ", g.comp(monthly=True), " per month (", new, " annualised). Retention bonus of ", g.comp(annual_lakh=r.randint(1, 8)),
          " is payable after 12 months.\n")
    b("\nWe value your contribution.\n\nWarm regards,\n", g.ref(hrh, "full"), "\nHead - People Operations\n")


@tpl("hr_free_text", "hr.exit_interview")
def t_exit(g):
    b, r = g.b, g.rng
    emp, mgr, hrb = g.subject(0.7), g.other(), g.other(1.0)
    he, his, him = g.pr(emp)
    b("EXIT INTERVIEW NOTES\nInterviewer: ", g.ref(hrb, "full"), "\nEmployee: ", g.ref(emp, "full"), " (", g.empid(emp), ")\nLast working day: ", g.date_txt(), "\n\n")
    k = r.randrange(3)
    cur, off = g.comp_seq(2)
    if k == 0:
        b("Reason for leaving: better compensation. ", g.ref(emp), " has an offer of ", off, " against current ", cur,
          ". Says ", g.ref(mgr, "hon_last"), " was supportive but budget was not available. Would consider returning. Personal email for alumni: ", g.email(emp), ".\n")
    elif k == 1:
        b(g.ref(emp), " cited lack of growth and ", r.choice(CONCERNS), ". ", he.capitalize(), " praised ", g.ref(mgr, "first"),
          " but felt the team lacked direction. Not eligible for rehire flag: no. FNF to bank account ", g.acct(), " IFSC ", g.ifsc(), ".\n")
    else:
        b(g.ref(emp), " ne bataya ki ", r.choice(["relocation", "higher studies", "startup offer"]), " ki wajah se ja rahe hain. Manager ", g.ref(mgr),
          " se koi issue nahi. Notice period buyout ", g.comp(monthly=True), " maang rahe hain. Contact: ", g.phone(emp), ".\n")


@tpl("hr_free_text", "hr.slack_msgs")
def t_slack(g):
    b, r = g.b, g.rng
    a, c, d = g.subject(0.7), g.other(), g.other()
    lines = [
        lambda: [f"[{r.randint(9, 18)}:{r.randint(10, 59)}] ", g.ref(a, "lower_first"), ": hey @", g.ref(c, "lower_first"), " can u check ", g.ref(d, "lower_first"), "'s leave balance? shows negative"],
        lambda: [f"[{r.randint(9, 18)}:{r.randint(10, 59)}] ", g.ref(c, "lower_first"), ": on it. btw ", g.ref(a, "first"), " asked about his hike letter, kab aayega?"],
        lambda: [f"[{r.randint(9, 18)}:{r.randint(10, 59)}] ", g.ref(a, "lower_first"), ": pls send reimbursement to my upi ", g.upi(a), " thx"],
        lambda: [f"[{r.randint(9, 18)}:{r.randint(10, 59)}] ", g.ref(d, "lower_first"), ": FYI ", g.ref(a, "first"), " is on ", r.choice(["sick leave", "WFH", "leave"]), " till ", g.date_txt(), ", ping on ", g.phone(a)],
        lambda: [f"[{r.randint(9, 18)}:{r.randint(10, 59)}] ", g.ref(a, "lower_first"), ": for the 12bb form my pan is ", g.pan(a, lower=0.5), ", uan pending"],
        lambda: [f"[{r.randint(9, 18)}:{r.randint(10, 59)}] ", g.ref(c, "lower_first"), ": ok will tell ", g.ref(d, "first_sfx"), ". @here reminder: timesheets due"],
    ]
    b("#hr-ops\n")
    for i in range(r.randint(3, 5)):
        b(r.choice(lines)(), "\n")


@tpl("hr_free_text", "hr.hinglish_chat")
def t_hinglish(g):
    b, r = g.b, g.rng
    a, c = g.subject(0.7), g.other()
    b(g.ref(c, "first"), ": ", g.ref(a, "first_sfx"), " ne kal offer letter dikhaya, usme CTC ", g.comp(plain=True), " likha hai par verbal mein ", g.comp(), " bola tha\n")
    b(g.ref(a, "lower_first"), ": haan yaar mera bhi yahi issue hai, HR ko mail kiya tha - ", g.email(a), " se\n")
    b(g.ref(c, "first"), ": theek hai, ", g.ref(a, "hon_last"), " ko bol dena ki ", g.mailbox(), " pe likhe. urgent ho toh mujhe call kar ", g.phone(c), "\n")


@tpl("hr_free_text", "hr.onboarding_docs")
def t_onboarding(g):
    b, r = g.b, g.rng
    emp, hrb = g.subject(0.7), g.other(1.0)
    b("Subject: Joining formalities - ", g.ref(emp, "full"), "\n\nDear ", g.ref(emp, "first"), ",\n\nWelcome aboard! Your date of joining is ", g.date_txt(), ". Please share the following before Day 1:\n")
    k = r.randrange(3)
    if k == 0:
        b("1. Aadhaar (for KYC): ", g.aadhaar(), "\n2. PAN: ", g.pan(emp), "\n3. Bank account no. ", g.acct(), " and IFSC ", g.ifsc(), "\n4. Date of birth as per certificate: ", g.dob(emp), "\n")
    elif k == 1:
        b("- Aadhaar number ", g.aadhaar(), "\n- PAN card no ", g.pan(emp), "\n- DOB ", g.dob(emp), "\n- Emergency contact number\n- Cancelled cheque\n")
    else:
        b("Reply to this mail with: UAN, PAN (", g.pan(emp), " on file?), and your bank a/c ", g.acct(), " / ", g.ifsc(), ". Your employee ID will be ", g.empid(emp), " and your official email ", g.email(emp), ".\n")
    b("\nRegards,\n", g.ref(hrb, "full"), "\nTalent Onboarding | ", g.phone(hrb), "\n")


@tpl("hr_free_text", "hr.candidate_note")
def t_candidate(g):
    b, r = g.b, g.rng
    cand = g.ext_person()
    rec = g.other(1.0)
    b("Candidate screening note - ", g.ref(rec, "full"), " (recruiter)\n\nCandidate: ", g.ref(cand, "full"), "\nEmail: ", g.email(cand), " | Mobile: ", g.phone(cand), "\n")
    cur, exp = g.comp_seq(2)
    b("Current CTC: ", cur, " | Expected: ", exp, " | Notice: ", r.choice(["30 days", "60 days", "90 days", "immediate"]), "\n")
    if r.random() < 0.6:
        b("DOB: ", g.dob(cand), " (as per resume)\n")
    b(g.ref(cand), " is ", r.choice(["strong on payroll", "a good culture fit", "overqualified", "flexible on location"]), ". ", g.ref(cand, "hon_last"), " can join within notice. Referred by ", g.ref(g.other(), "init"), ".\n")


@tpl("hr_free_text", "hr.show_cause")
def t_show_cause(g):
    b, r = g.b, g.rng
    emp, hrh = g.subject(0.7), g.other(1.0)
    b("SHOW CAUSE NOTICE\n\nTo: ", g.ref(emp, "full"), "\nEmp. Code: ", g.empid(emp), "\nDate: ", g.date_txt(), "\n\n")
    b("It has come to our notice that ", g.ref(emp, "hon_last"), f" was absent without intimation on {g.date_txt()} and {g.date_txt()}. You are required to explain in writing within 48 hours why disciplinary action should not be initiated.\n\n")
    b("Reply to ", g.email(hrh), ".\n\n", g.ref(hrh, "full"), "\nHR Manager\n")


@tpl("hr_free_text", "hr.card_reimbursement")
def t_card(g):
    b, r = g.b, g.rng
    emp, fin = g.subject(0.7), g.other(1.0)
    b("From: ", g.email(emp), "\nSubject: Corporate card statement mismatch\n\n")
    k = r.randrange(3)
    if k == 0:
        b("Hi ", g.ref(fin, "first"), ", my corporate card ", g.card(), " shows a charge of Rs. ", inr_group(r.randint(500, 60000)), " on ", g.date_txt(), " that I do not recognise. Please raise with the bank. Employee ", g.empid(emp), ".\n")
    elif k == 1:
        b("Please reimburse the travel claim (Rs. ", inr_group(r.randint(800, 25000)), ") to my UPI id ", g.upi(emp), " instead of the card. The card on file ending ", str(r.randint(1000, 9999)), " has expired; new one is ", g.card(), ".\n")
    else:
        b("hi ", g.ref(fin, "first_sfx"), ", client dinner bill paid on personal card ", g.card(), " (expiry ", f"{r.randint(1, 12):02d}/{r.randint(27, 31)}", "). amount Rs ", inr_group(r.randint(2000, 30000)), ", please process\n")
    b("\nThanks, ", g.ref(emp, "full"), "\n")


@tpl("hr_free_text", "hr.bgv")
def t_bgv(g):
    b, r = g.b, g.rng
    emp = g.subject(0.6)
    b("BACKGROUND VERIFICATION - SUMMARY\nCandidate name: ", g.ref(emp, "upper_full"), "\nFather's name: ", g.ref(g.ext_person(), "full"), "\n")
    b("Date of Birth: ", g.dob(emp), "\nPAN: ", g.pan(emp), "\nAadhaar: ", g.aadhaar(), "\nContact: ", g.phone(emp), "\n")
    b("Employment check: ", r.choice(["Verified", "Discrepancy in tenure", "Pending"]), " | Education: ", r.choice(["Verified", "Pending"]), "\nVerifier comments: ", g.ref(emp, "hon_last"), " cooperated. Employee ID assigned: ", g.empid(emp), ".\n")


@tpl("hr_free_text", "hr.medical_leave")
def t_medical(g):
    b, r = g.b, g.rng
    emp, mgr, fam = g.subject(0.7), g.other(), g.ext_person()
    he, his, him = g.pr(emp)
    b("Confidential - medical leave\n\n", g.ref(emp, "full"), " (", g.empid(emp), ") has requested extended sick leave from ", g.date_txt(), f" for {r.choice(['a surgery', 'treatment for a chronic condition', 'a fracture', 'maternity-related complications'])}. ")
    b("Doctor's certificate received. ", g.ref(mgr, "hon_last"), " has been told only that it is medical. Emergency contact: ", g.ref(fam, "full"), f" ({r.choice(['spouse', 'father', 'mother', 'brother', 'wife'])}), ", g.phone(fam), ". ")
    b(g.ref(emp, "first"), " may be reached at ", g.email(emp), " for paperwork.\n")


@tpl("hr_free_text", "hr.referral")
def t_referral(g):
    b, r = g.b, g.rng
    ref_, cand = g.subject(0.8), g.ext_person()
    b("Referral bonus tracker\n\n", g.ref(ref_, "full"), " (", g.empid(ref_), ") referred ", g.ref(cand, "full"), " who joined on ", g.date_txt(), ". ")
    b("Bonus of ", g.comp(monthly=True), " is payable after 90 days. ", g.ref(ref_, "first"), "'s account: ", g.acct(), ", ", g.ifsc(), ". Candidate ", g.ref(cand, "hon_last"), " confirmed via ", g.email(cand), ".\n")


@tpl("hr_free_text", "hr.promotion")
def t_promotion(g):
    b, r = g.b, g.rng
    emp, mgr = g.subject(0.7), g.other()
    b("Promotion recommendation\n\n", g.ref(emp, "full"), " (", g.empid(emp), "): ", r.choice(["L3 to L4", "Senior Analyst to Lead", "Manager to Senior Manager", "SDE-2 to SDE-3"]), ". ")
    cur, new = g.comp_seq(2)
    b("Fixed pay moves from ", cur, " to ", new, "; variable target ", r.choice(["10%", "15%", "20%"]), ". Recommended by ", g.ref(mgr, "full"), ", endorsed by ", g.ref(g.other(), "hon_full"), ". ")
    b("Pending calibration. ", g.ref(emp, "hon_last"), " has not been informed.\n")


@tpl("hr_free_text", "hr.payroll_query")
def t_payroll_query(g):
    b, r = g.b, g.rng
    emp, pr = g.subject(0.7), g.other(1.0)
    b("Ticket PAY-", str(r.randint(10000, 99999)), " | Requester: ", g.ref(emp, "full"), " | ", g.empid(emp), "\n")
    b(g.ref(emp), " says the payslip for ", r.choice(_MONTHS), " shows net pay ", g.comp(monthly=True), " but the bank credit was lower. TDS deducted looks wrong. PAN on record: ", g.pan(emp), ". ")
    b(g.ref(pr, "full"), " to check and revert. Credited to a/c ", g.acct(), " (", g.ifsc(), ").\n")


@tpl("hr_free_text", "hr.pip")
def t_pip(g):
    b, r = g.b, g.rng
    emp, mgr, hrb = g.subject(0.7), g.other(), g.other(1.0)
    b("PERFORMANCE IMPROVEMENT PLAN\n\nEmployee: ", g.ref(emp, "hon_full"), "\nManager: ", g.ref(mgr, "init"), "\nHRBP: ", g.ref(hrb, "full"), "\nDuration: 60 days from ", g.date_txt(), "\n\n")
    b("Areas of concern: ", r.choice(CONCERNS), "; quality of deliverables on ", r.choice(PROJECTS), ". ", g.ref(emp, "hon_last"), " acknowledged the plan. ")
    b("Weekly check-ins with ", g.ref(mgr, "first"), ". Outcome will be shared with ", g.ref(emp, "first"), " on ", g.date_txt(), ".\n")


@tpl("hr_free_text", "hr.relocation")
def t_reloc(g):
    b, r = g.b, g.rng
    emp = g.subject(0.7)
    b("Relocation approval\n\n", g.ref(emp, "full"), " - ", g.empid(emp), " - relocating to ", r.choice(["Pune", "Bengaluru", "Hyderabad", "Gurugram", "Chennai"]), " effective ", g.date_txt(), ". ")
    b("Relocation allowance ", g.comp(monthly=True), " one-time; revised CTC ", g.comp(), " (location adjusted). ", g.ref(emp, "first"), " will confirm the new address via ", g.email(emp), ".\n")


@tpl("hr_free_text", "hr.teams_chat")
def t_teams(g):
    b, r = g.b, g.rng
    a, c = g.subject(0.7), g.other()
    b(f"{r.choice(['Mon', 'Tue', 'Wed', 'Thu', 'Fri'])} {r.randint(9, 18)}:{r.randint(10, 59)} ", g.ref(a, "full"), "\nhi ", g.ref(c, "first"), ", quick one - my DOB is wrong in HRMS\n")
    b("it should be ", g.dob(a), " not ", fmt_date(g.dob_date(a) + timedelta(days=r.randint(30, 700)), None, r), "\n")
    b(f"{r.randint(9, 18)}:{r.randint(10, 59)} ", g.ref(c, "full"), "\nsure ", g.ref(a, "first"), ", send me the aadhaar copy, will update\n")


@tpl("hr_free_text", "hr.recommendation")
def t_recommendation(g):
    b, r = g.b, g.rng
    cand = g.ext_person(mono=True) if r.random() < 0.5 else g.ext_person()
    mgr = g.other()
    b("Reference check call - ", g.date_txt(), "\nSpoke to ", g.ref(mgr, "hon_full"), " regarding ", g.ref(cand, "full"), ".\n")
    b(g.ref(mgr, "hon_last"), " said ", g.ref(cand, "first"), " was ", r.choice(["reliable and detail oriented", "good technically but needed guidance", "a top performer"]), ". Would rehire: ", r.choice(["yes", "yes", "no"]), ". ")
    b("Last drawn CTC as told by ", g.ref(cand, "first"), ": ", g.comp(), ". Referee's number: ", g.phone(mgr), ".\n")


@tpl("hr_free_text", "hr.emergency_contact")
def t_emergency(g):
    b, r = g.b, g.rng
    emp = g.subject(0.7)
    f1, f2 = g.ext_person(), g.ext_person()
    b("Emergency contact update\nEmployee: ", g.ref(emp, "full"), " (", g.empid(emp), ")\n")
    b("Primary: ", g.ref(f1, "full"), " (", r.choice(["spouse", "father", "mother", "brother", "sister"]), ") - ", g.phone(f1), "\n")
    b("Secondary: ", g.ref(f2, "full"), " (", r.choice(["friend", "uncle", "roommate", "neighbour"]), ") - ", g.phone(f2), "\n")
    b("Verified by phone on ", g.date_txt(), ".\n")


@tpl("hr_free_text", "hr.dob_correction")
def t_dob_fix(g):
    b, r = g.b, g.rng
    emp, hrb = g.subject(0.7), g.other(1.0)
    wrong = fmt_date(g.dob_date(emp) + timedelta(days=r.randint(1, 900)), None, r)
    b("Subject: Date of birth correction\n\nHi ", g.ref(hrb, "first"), ",\n\n")
    k = r.randrange(3)
    if k == 0:
        b("My date of birth is wrongly recorded as ", E(wrong, "DATE_OF_BIRTH"), " in the HRMS. As per my Aadhaar and school certificate I was born on ", g.dob(emp), ". Please correct it. Employee ID ", g.empid(emp), ".\n")
    elif k == 1:
        b("Please note DOB of ", g.ref(emp, "full"), " is ", g.dob(emp), ", not ", E(wrong, "DATE_OF_BIRTH"), ". Documents attached. This affects the retirement date and PF.\n")
    else:
        b("mera janm tithi ", g.dob(emp), " hai, system mein galat hai. Aadhaar ", g.aadhaar(), " ke hisaab se correct kar dijiye.\n")
    b("\nThanks,\n", g.ref(emp, "first"), "\n")


@tpl("hr_free_text", "hr.slack_creds")
def t_slack_creds(g):
    b, r = g.b, g.rng
    a, c = g.subject(0.8), g.other()
    b("[", f"{r.randint(9, 18)}:{r.randint(10, 59)}", "] ", g.ref(a, "lower_first"), ": ", g.ref(c, "first"), " the payroll portal login for the interim run is user ", g.email(a), " pass ", g.secret("password"), " pls dont forward\n")
    b("[", f"{r.randint(9, 18)}:{r.randint(10, 59)}", "] ", g.ref(c, "lower_first"), ": got it. and the sftp key? ", g.ref(a, "first_sfx"), " said it is ", g.secret(r.choice(["ghp", "sk", "akia"])), "\n")


@tpl("hr_free_text", "hr.offer_negotiation")
def t_offer(g):
    b, r = g.b, g.rng
    cand, rec = g.ext_person(), g.other(1.0)
    b("Subject: Offer - ", g.ref(cand, "full"), "\n\nHi ", g.ref(rec, "first"), ",\n\n")
    cur, off, ask = g.comp_seq(3)
    b(g.ref(cand, "hon_last"), " is asking for ", ask, " fixed against our offer of ", off, ". Current CTC ", cur, ". ")
    b("Joining bonus of ", g.comp(monthly=True), " approved. ", g.ref(cand, "first"), " can be reached at ", g.phone(cand), " or ", g.email(cand), ". Grade: ", r.choice(["G7", "L4", "Band 5"]), ".\n")


@tpl("hr_free_text", "hr.attrition_review")
def t_attrition(g):
    b, r = g.b, g.rng
    b("Monthly attrition review - ", r.choice(_MONTHS), " ", str(r.choice([2025, 2026])), "\n\n")
    for i in range(r.randint(2, 3)):
        p = g.subject(0.7)
        b(f"{i + 1}. ", g.ref(p, "full"), " (", g.empid(p), ") - ", r.choice(["resigned", "absconding", "terminated", "transferred"]), ", CTC ", g.comp(), ", reason: ", r.choice(["better offer", "personal", "relocation", "manager issue"]), ". ")
        if r.random() < 0.5:
            b("HRBP spoke with ", g.ref(p, "first"), ". ")
        b("\n")
    b("Total regretted exits: ", str(r.randint(1, 9)), ".\n")


@tpl("hr_free_text", "hr.hrbp_handover")
def t_handover(g):
    b, r = g.b, g.rng
    a, c, e1, e2 = g.other(1.0), g.other(1.0), g.subject(0.7), g.subject(0.7)
    b("HRBP handover notes\n\n", g.ref(a, "full"), " -> ", g.ref(c, "full"), "\n\nOpen items:\n")
    b("* ", g.ref(e1, "hon_full"), ": grievance vs manager, follow up ", g.date_txt(), ". Direct line ", g.phone(e1), ".\n")
    b("* ", g.ref(e2, "init"), ": salary correction pending, new CTC ", g.comp(), ", employee id ", g.empid(e2), ".\n")
    b("* ", g.ref(e1, "first"), " prefers email (", g.email(e1), ") over calls.\n\nPing ", g.ref(a, "first"), " if unclear.\n")


# --------------------------------------------------------------------------- HRIS structured
def nm(p, part="full"):
    """PERSON entity for one name field; '' (plain) when the part does not exist (mononym)."""
    if part == "full":
        return E(p.full, "PERSON", p.source)
    if part == "first":
        return E(p.first, "PERSON", p.source)
    if p.mono or not p.last:
        return ""
    return E(p.last, "PERSON", p.source)


def annual_int(g):
    return round(g.rng.randint(30, 900) / 10 * 100000)


def xt(b, ind, tag, val):
    b(" " * ind, f"<{tag}>", val, f"</{tag}>\n")


def csv_cell(b, cell, delim=","):
    s = cell.v if isinstance(cell, E) else str(cell)
    if any(c in s for c in (delim, '"', "\n")):
        assert '"' not in s
        b('"', cell, '"')
    else:
        b(cell)


def csv_rows(b, header, rows, delim=",", eol="\n"):
    for line in [header] + rows:
        for i, c in enumerate(line):
            if i:
                b(delim)
            csv_cell(b, c, delim)
        b(eol)


DEPTS = ["Finance", "Engineering", "Human Resources", "Sales", "Operations", "Legal", "IT Infrastructure", "Procurement", "Customer Support"]
LOCS = ["Pune", "Bengaluru", "Mumbai", "Hyderabad", "Gurugram", "Chennai", "Kolkata", "Noida"]
COLS = {
    "id": ["Employee ID", "Emp ID", "Employee_ID", "EmpCode", "Worker ID", "Emp No"],
    "name": ["Employee Name", "Full Name", "Name", "Legal Name", "Employee"],
    "email": ["Email", "Work Email", "E-mail", "Official Email ID", "Email Address"],
    "phone": ["Mobile", "Phone", "Contact No", "Mobile Number", "Phone Number"],
    "dob": ["Date of Birth", "DOB", "Birth Date", "D.O.B"],
    "pan": ["PAN", "PAN Number", "PAN No"],
    "aadhaar": ["Aadhaar", "Aadhaar Number", "UID", "Aadhar No"],
    "acct": ["Bank Account", "Account No", "A/C Number", "Bank A/c No", "Account Number"],
    "ifsc": ["IFSC", "IFSC Code", "Bank IFSC"],
    "ctc": ["CTC", "Annual CTC", "Fixed Pay", "Annual Salary", "Gross Annual"],
    "upi": ["UPI ID", "VPA", "UPI"],
    "manager": ["Manager", "Reporting Manager", "Supervisor"],
}


def csv_table(g, keys, nrows, delim=",", filler=True):
    r = g.rng
    nstyle = r.choice(["full", "last_first", "upper_full", "full"])
    dstyle = r.randrange(8)
    cmode = r.choice(["plain", "symbol", "grouped"])
    keys = list(keys)
    extra = []
    if filler:
        extra = r.sample(["dept", "loc", "doj", "grade", "status"], r.randint(1, 3))
    allk = keys + extra
    r.shuffle(allk)
    header = [r.choice(COLS[k]) if k in COLS else {"dept": "Department", "loc": "Location", "doj": "Date of Joining",
                                                    "grade": "Grade", "status": "Status"}[k] for k in allk]
    rows = []
    for _ in range(nrows):
        p = g.subject(0.7)
        row = []
        for k in allk:
            if k == "id":
                row.append(g.empid(p))
            elif k == "name":
                row.append(g.ref(p, nstyle)[0])
            elif k == "email":
                row.append(g.email(p))
            elif k == "phone":
                row.append(g.phone(p))
            elif k == "dob":
                row.append(g.dob(p, dstyle))
            elif k == "pan":
                row.append(g.pan(p))
            elif k == "aadhaar":
                row.append(g.aadhaar())
            elif k == "acct":
                row.append(g.acct())
            elif k == "ifsc":
                row.append(g.ifsc())
            elif k == "ctc":
                x = r.randint(30, 900) / 10
                a = round(x * 100000)
                row.append(E({"plain": str(a), "symbol": f"₹{inr_group(a)}", "grouped": inr_group(a)}[cmode], "COMPENSATION"))
            elif k == "upi":
                row.append(g.upi(p))
            elif k == "manager":
                row.append(g.ref(g.other(0.9), "full")[0])
            elif k == "dept":
                row.append(r.choice(DEPTS))
            elif k == "loc":
                row.append(r.choice(LOCS))
            elif k == "doj":
                row.append(g.date_txt())
            elif k == "grade":
                row.append(r.choice(["L3", "L4", "L5", "G7", "G8", "Band 4"]))
            elif k == "status":
                row.append(r.choice(["Active", "Active", "On Leave", "Notice Period"]))
        rows.append(row)
    return header, rows


@tpl("hris_structured", "hris.wd_workers_rest")
def t_wd_rest(g):
    b, r = g.b, g.rng
    n = r.randint(1, 3)
    data = []
    for _ in range(n):
        p, mgr = g.subject(0.7), g.other(0.9)
        w = {"id": g.hexs(32), "descriptor": nm(p), "workerType": {"descriptor": r.choice(["Employee", "Employee", "Contingent Worker"])},
             "employeeID": g.empid(p),
             "person": {"legalName": {"firstName": nm(p, "first"), "lastName": nm(p, "last")},
                        "dateOfBirth": g.dob(p, 2),
                        "nationalIds": [{"type": "India Aadhaar", "id": g.aadhaar()}, {"type": "India PAN", "id": g.pan(p)}],
                        "contact": {"primaryWorkEmail": g.email(p), "mobilePhone": g.phone(p)}},
             "position": {"businessTitle": r.choice(["Analyst", "Senior Engineer", "HR Executive", "Manager - Finance"]),
                          "supervisor": {"descriptor": nm(mgr)}, "location": r.choice(LOCS)},
             "hireDate": fmt_date(g.rand_date(2015, 2025), 2, r)}
        if r.random() < 0.5:
            w["compensation"] = {"annualBasePay": {"amount": num(str(annual_int(g)), "COMPENSATION"), "currency": "INR"}}
        data.append(w)
    jdump(b, {"total": n, "data": data}, indent=r.choice([2, 2, 4]))
    b("\n")


@tpl("hris_structured", "hris.wd_worker_raas")
def t_wd_raas(g):
    b, r = g.b, g.rng
    p, mgr = g.subject(0.7), g.other(0.9)
    obj = {"Report_Entry": [{
        "Employee_ID": g.empid(p, r.choice(["WD", "EMP"])), "Legal_First_Name": nm(p, "first"), "Legal_Last_Name": nm(p, "last"),
        "Preferred_Name": nm(p), "Manager": nm(mgr), "Date_of_Birth": g.dob(p, r.choice([0, 1, 3])),
        "Work_Email": g.email(p), "Mobile_Phone": g.phone(p), "National_ID": g.pan(p), "Original_Hire_Date": fmt_date(g.rand_date(2012, 2025), 0, r),
        "Cost_Center": f"CC-{r.randint(1000, 9999)}", "Job_Profile": r.choice(["Payroll Specialist", "Software Engineer II", "Talent Partner"])}]}
    jdump(b, obj, indent=2)
    b("\n")


@tpl("hris_structured", "hris.ukg_employee_json")
def t_ukg(g):
    b, r = g.b, g.rng
    p = g.subject(0.7)
    obj = {"employeeNumber": g.empid(p, r.choice(["E", "E", "WD"])), "firstName": nm(p, "first"), "lastName": nm(p, "last"),
           "dateOfBirth": E(g.dob_date(p).isoformat() + "T00:00:00", "DATE_OF_BIRTH") if r.random() < 0.5 else g.dob(p, 2),
           "emailAddress": g.email(p), "homePhone": g.phone(p), "nationalId": g.pan(p) if r.random() < 0.6 else g.aadhaar(),
           "employmentStatus": r.choice(["A", "A", "T", "L"]), "orgLevel1": r.choice(DEPTS), "annualSalary": num(str(annual_int(g)), "COMPENSATION"),
           "payGroup": r.choice(["IN-MTHLY", "IN-MGMT"]), "lastHireDate": g.rand_date(2010, 2025).isoformat() + "T00:00:00"}
    jdump(b, obj, indent=r.choice([2, None]))
    b("\n")


@tpl("hris_structured", "hris.comp_change_json")
def t_comp_change(g):
    b, r = g.b, g.rng
    p, mgr = g.subject(0.7), g.other(0.9)
    cur, new = g.comp_seq(2)
    obj = {"event": "Request_Compensation_Change", "worker": {"employeeId": g.empid(p), "name": nm(p)},
           "effectiveDate": g.rand_date().isoformat(), "reason": r.choice(["Merit", "Promotion", "Market Adjustment", "Annual Review"]),
           "proposedBy": {"name": nm(mgr)},
           "components": [{"plan": "Base Salary", "previous": cur, "proposed": new, "frequency": "Annual"},
                          {"plan": "Variable Pay Target", "proposed": g.comp(annual_lakh=r.randint(1, 15)), "frequency": "Annual"}],
           "currency": "INR"}
    jdump(b, obj, indent=r.choice([2, 4]))
    b("\n")


@tpl("hris_structured", "hris.comp_history_compact")
def t_comp_hist(g):
    b, r = g.b, g.rng
    p = g.subject(0.7)
    hist = []
    for i in range(r.randint(2, 4)):
        hist.append({"eff": fmt_date(g.rand_date(2018, 2026), 2, r), "base": num(str(annual_int(g)), "COMPENSATION"),
                     "bonus": num(str(r.randrange(20000, 900000, 5000)), "COMPENSATION"), "type": r.choice(["HIRE", "MERIT", "PROMO"])})
    jdump(b, {"emp": g.empid(p), "name": nm(p), "hist": hist}, indent=None)
    b("\n")


@tpl("hris_structured", "hris.absence_xml")
def t_absence_xml(g):
    b, r = g.b, g.rng
    b('<?xml version="1.0" encoding="UTF-8"?>\n<wd:Absence_Entries xmlns:wd="urn:com.workday/bsvc">\n')
    for _ in range(r.randint(1, 3)):
        p = g.subject(0.7)
        b("  <wd:Absence_Entry>\n")
        b("    <wd:Worker>\n")
        xt(b, 6, "wd:Employee_ID", g.empid(p))
        xt(b, 6, "wd:Worker_Name", nm(p))
        b("    </wd:Worker>\n")
        xt(b, 4, "wd:Type", r.choice(["Sick Leave", "Earned Leave", "Casual Leave", "Maternity Leave", "Comp Off"]))
        d1, d2 = g.date_range(iso=True)
        xt(b, 4, "wd:From", d1)
        xt(b, 4, "wd:To", d2)
        xt(b, 4, "wd:Days", str(r.randint(1, 15)))
        if r.random() < 0.5:
            xt(b, 4, "wd:Comment", r.choice(["fever and cold", "surgery scheduled", "attending wedding", "family emergency"]))
        if r.random() < 0.3:
            xt(b, 4, "wd:Contact_Phone", g.phone(p))
        b("  </wd:Absence_Entry>\n")
    b("</wd:Absence_Entries>\n")


@tpl("hris_structured", "hris.worker_xml_soap")
def t_worker_xml(g):
    b, r = g.b, g.rng
    p, mgr = g.subject(0.7), g.other(0.9)
    b('<?xml version="1.0" encoding="UTF-8"?>\n<wd:Get_Workers_Response xmlns:wd="urn:com.workday/bsvc" wd:version="v41.0">\n  <wd:Response_Data>\n    <wd:Worker>\n      <wd:Worker_Data>\n')
    xt(b, 8, "wd:Worker_ID", g.empid(p))
    b("        <wd:Personal_Data>\n          <wd:Name_Data>\n")
    xt(b, 12, "wd:First_Name", nm(p, "first"))
    if nm(p, "last"):
        xt(b, 12, "wd:Last_Name", nm(p, "last"))
    b("          </wd:Name_Data>\n")
    xt(b, 10, "wd:Birth_Date", g.dob(p, 2))
    b("          <wd:Identification_Data>\n")
    b(f'            <wd:National_ID wd:Type="IN-PAN">', g.pan(p), "</wd:National_ID>\n")
    b(f'            <wd:National_ID wd:Type="IN-AADHAAR">', g.aadhaar(), "</wd:National_ID>\n")
    b("          </wd:Identification_Data>\n          <wd:Contact_Data>\n")
    xt(b, 12, "wd:Email_Address", g.email(p))
    xt(b, 12, "wd:Phone_Number", g.phone(p))
    b("          </wd:Contact_Data>\n        </wd:Personal_Data>\n        <wd:Employment_Data>\n")
    xt(b, 10, "wd:Manager", nm(mgr))
    xt(b, 10, "wd:Business_Title", r.choice(["Senior Analyst", "Team Lead", "Associate Director"]))
    if r.random() < 0.6:
        b("          <wd:Compensation><wd:Total_Base_Pay wd:Currency=\"INR\" wd:Frequency=\"Annual\">", E(str(annual_int(g)), "COMPENSATION"), "</wd:Total_Base_Pay></wd:Compensation>\n")
    b("        </wd:Employment_Data>\n      </wd:Worker_Data>\n    </wd:Worker>\n  </wd:Response_Data>\n</wd:Get_Workers_Response>\n")


@tpl("hris_structured", "hris.csv_employee_export")
def t_csv_emp(g):
    keys = g.rng.sample(["email", "phone", "dob", "pan", "ctc", "manager"], g.rng.randint(2, 4))
    h, rows = csv_table(g, ["id", "name"] + keys, g.rng.randint(3, 8))
    csv_rows(g.b, h, rows, eol=g.rng.choice(["\n", "\r\n"]))


@tpl("hris_structured", "hris.csv_bank_advice")
def t_csv_bank(g):
    r = g.rng
    keys = ["name", "acct", "ifsc"] + r.sample(["id", "upi", "pan"], r.randint(0, 2))
    h, rows = csv_table(g, keys, r.randint(3, 8), filler=False)
    # amount column: net pay credited (compensation)
    h.append(r.choice(["Amount", "Net Pay", "Amount (INR)", "Salary Credit"]))
    narr = r.choice(["SAL SEP 2026", "SALARY-SEP26", "Payroll Sep", "SAL/AUG/26"])
    h.append("Narration")
    for row in rows:
        row.append(E(inr_group(r.randrange(20000, 400000, 100)) if r.random() < 0.5 else f"{r.randrange(20000, 400000, 100)}.00", "COMPENSATION"))
        row.append(narr)
    csv_rows(g.b, h, rows)


@tpl("hris_structured", "hris.csv_corp_cards")
def t_csv_cards(g):
    r = g.rng
    b = g.b
    hdr = ["Cardholder", "Employee ID", "Card Number", "Expiry", "Credit Limit", "Status"]
    rows = []
    for _ in range(r.randint(3, 7)):
        p = g.subject(0.7)
        rows.append([g.ref(p, "upper_full")[0], g.empid(p), g.card(), f"{r.randint(1, 12):02d}/{r.randint(26, 31)}",
                     inr_group(r.randrange(50000, 1000000, 25000)), r.choice(["Active", "Blocked", "Active"])])
    csv_rows(b, hdr, rows)


@tpl("hris_structured", "hris.tsv_dump")
def t_tsv(g):
    keys = g.rng.sample(["email", "phone", "dob", "aadhaar", "pan", "acct", "ifsc", "ctc", "upi"], g.rng.randint(3, 5))
    h, rows = csv_table(g, ["id", "name"] + keys, g.rng.randint(3, 6), delim="\t")
    csv_rows(g.b, h, rows, delim="\t")


@tpl("hris_structured", "hris.bank_details_json")
def t_bank_json(g):
    b, r = g.b, g.rng
    p = g.subject(0.7)
    accts = []
    for i in range(r.randint(1, 2)):
        a = {"accountHolder": nm(p), "bankName": r.choice(["HDFC Bank", "ICICI Bank", "State Bank of India", "Axis Bank", "Kotak Mahindra Bank"]),
             "accountNumber": g.acct(), "ifscCode": g.ifsc(), "accountType": r.choice(["SAVINGS", "SALARY"]), "primary": i == 0}
        if r.random() < 0.4:
            a["upiId"] = g.upi(p)
        accts.append(a)
    jdump(b, {"workerId": g.empid(p), "bankAccounts": accts, "lastVerified": g.rand_date().isoformat()}, indent=r.choice([2, None]))
    b("\n")


@tpl("hris_structured", "hris.dependents_json")
def t_dependents(g):
    b, r = g.b, g.rng
    p = g.subject(0.7)
    deps = []
    for _ in range(r.randint(1, 3)):
        d = g.ext_person()
        deps.append({"name": nm(d), "relationship": r.choice(["Spouse", "Child", "Father", "Mother"]),
                     "dateOfBirth": g.dob(d, 2), "gender": d.gender, "coveredUnderPolicy": r.random() < 0.8,
                     **({"aadhaar": g.aadhaar()} if r.random() < 0.3 else {})})
    jdump(b, {"employeeId": g.empid(p), "employee": nm(p), "benefitPlan": r.choice(["GMC-2026", "GPA-Plus", "Family Floater 5L"]), "dependents": deps}, indent=2)
    b("\n")


@tpl("hris_structured", "hris.offer_json")
def t_offer_json(g):
    b, r = g.b, g.rng
    cand, rec = g.ext_person(), g.other(1.0)
    obj = {"requisition": f"REQ-{r.randint(10000, 99999)}", "candidate": {"name": nm(cand), "email": g.email(cand), "phone": g.phone(cand)},
           "offer": {"designation": r.choice(["Senior Engineer", "Lead Analyst", "Manager"]), "fixedCTC": g.comp(), "variable": g.comp(annual_lakh=r.randint(1, 12)),
                     "joiningBonus": g.comp(monthly=True), "doj": g.rand_date(2026, 2026).isoformat()},
           "recruiter": nm(rec), "status": r.choice(["Extended", "Accepted", "Negotiation"])}
    jdump(b, obj, indent=2)
    b("\n")


@tpl("hris_structured", "hris.audit_events_json")
def t_audit(g):
    b, r = g.b, g.rng
    ev = []
    for _ in range(r.randint(2, 3)):
        p, actor = g.subject(0.7), g.other(1.0)
        e = {"ts": f"{g.rand_date().isoformat()}T{r.randint(0, 23):02d}:{r.randint(0, 59):02d}:{r.randint(0, 59):02d}Z", "actor": nm(actor),
             "action": r.choice(["Change Bank Account", "Change Personal Data", "Request Compensation Change"]), "target": {"id": g.empid(p), "name": nm(p)}}
        k = r.randrange(3)
        if k == 0:
            e["from"], e["to"] = g.acct(), g.acct()
        elif k == 1:
            e["from"], e["to"] = g.comp_seq(2)
        else:
            e["field"], e["new_value"] = "Birth_Date", g.dob(p, 2)
        ev.append(e)
    jdump(b, {"events": ev}, indent=2)
    b("\n")


@tpl("hris_structured", "hris.leave_balance_csv")
def t_leave_csv(g):
    r = g.rng
    h, rows = csv_table(g, ["id", "name"], r.randint(4, 8), filler=False)
    h += ["Leave Type", "Opening", "Availed", "Balance"]
    for row in rows:
        o = r.randint(6, 24)
        a = r.randint(0, o)
        row += [r.choice(["EL", "SL", "CL"]), str(o), str(a), str(o - a)]
    csv_rows(g.b, h, rows)


# --------------------------------------------------------------------------- integration / debug
def ts(g, sep=" "):
    """Monotonic per-item clock so multi-line logs read like real logs."""
    r = g.rng
    clk = g.cache.get("clock")
    if clk is None:
        clk = datetime(2026, 1, 1) + timedelta(seconds=r.randint(0, 260 * 86400))
    else:
        clk += timedelta(seconds=r.randint(1, 45))
    g.cache["clock"] = clk
    return clk.strftime(f"%Y-%m-%d{sep}%H:%M:%S")


TENANT_HOST = "impl-services1.wd102.myworkday.com"


@tpl("integration_debug", "int.py_traceback")
def t_py_tb(g):
    b, r = g.b, g.rng
    p = g.subject(0.7)
    b(ts(g), f",{r.randint(100, 999)} ERROR [hr-sync-worker-{r.randint(1, 8)}] hrsync.push - push failed for worker ", g.empid(p), "\n")
    b('Traceback (most recent call last):\n  File "/opt/hrsync/app/sync.py", line ', str(r.randint(40, 200)), ", in run\n    push_worker(session, w)\n")
    b('  File "/opt/hrsync/app/workday.py", line ', str(r.randint(100, 400)), ", in push_worker\n    resp.raise_for_status()\n")
    k = r.randrange(5)
    if k == 0:
        b("ValueError: invalid PAN '", g.pan(p), "' for worker ", g.empid(p), " (name=", nm(p), ")\n")
    elif k == 1:
        b('psycopg2.errors.UniqueViolation: duplicate key value violates unique constraint "uq_employee_email"\nDETAIL:  Key (email)=(', g.email(p), ") already exists.\n")
    elif k == 2:
        b("KeyError: 'ifsc'\nDuring handling of the above exception, another exception occurred:\n  payload={'employee_id': '", g.empid(p), "', 'account_no': '", g.acct(), "', 'holder': '", nm(p), "'}\n")
    elif k == 3:
        b(f"requests.exceptions.HTTPError: 401 Client Error: Unauthorized for url: https://{TENANT_HOST}/ccx/api/v1/kestrel/workers\n")
        b("  request headers: {'Authorization': 'Bearer ", g.secret("bearer"), "', 'Accept': 'application/json'}\n")
    else:
        b("requests.exceptions.HTTPError: 400 Client Error: Bad Request - {\"error\":\"invalid_field\",\"field\":\"dateOfBirth\",\"value\":\"", g.dob(p, 2), "\"}\n")


@tpl("integration_debug", "int.java_trace")
def t_java_tb(g):
    b, r = g.b, g.rng
    p = g.subject(0.7)
    b(ts(g, "T"), f".{r.randint(100, 999)}+05:30 ERROR {r.randint(1000, 30000)} --- [http-nio-8080-exec-{r.randint(1, 20)}] c.k.payroll.api.WorkerController : Request failed\n")
    k = r.randrange(3)
    if k == 0:
        b("java.lang.IllegalStateException: Bank verification failed for account ", g.acct(), " (IFSC ", g.ifsc(), "), holder=", nm(p), "\n")
    elif k == 1:
        b("com.kestrel.payroll.ValidationException: Aadhaar ", g.aadhaar(), " linked to multiple employees: ", g.empid(p), ", ", g.empid(), "\n")
    else:
        b("org.springframework.web.client.HttpClientErrorException$Unauthorized: 401 : \"{\"message\":\"token rejected\",\"token\":\"", g.secret("bearer"), "\"}\"\n")
    for fr in ["com.kestrel.payroll.service.BankVerifier.verify(BankVerifier.java:141)", "com.kestrel.payroll.api.WorkerController.update(WorkerController.java:88)",
               "java.base/jdk.internal.reflect.DirectMethodHandleAccessor.invoke(DirectMethodHandleAccessor.java:103)"]:
        b("\tat ", fr, "\n")


@tpl("integration_debug", "int.http_dump")
def t_http_dump(g):
    b, r = g.b, g.rng
    p = g.subject(0.7)
    m = r.choice(["POST", "PUT", "PATCH"])
    b(f"{m} /ccx/api/v1/kestrel/workers/", g.empid(p), " HTTP/1.1\nHost: ", TENANT_HOST, "\n")
    k = r.randrange(3)
    if k == 0:
        b("Authorization: Bearer ", g.secret("bearer"), "\n")
    elif k == 1:
        cred = base64.b64encode(f"svc_hr_sync:{g.password()}".encode()).decode()
        b("Authorization: Basic ", E(cred, "SECRET"), "\n")
    else:
        b("Authorization: Bearer ", g.secret("jwt"), "\n")
    b("Content-Type: application/json\nUser-Agent: hrsync/2.4.1 python-requests/2.31.0\nX-Request-Id: ", g.uuid(), f"\nContent-Length: {r.randint(120, 900)}\n")
    if r.random() < 0.4:
        b("X-Api-Key: ", g.secret(r.choice(["sk", "awssecret", "bearer"])), "\n")
    b("\n")
    body = {"employeeId": g.empid(p), "name": nm(p), "email": g.email(p), "mobile": g.phone(p)}
    if r.random() < 0.5:
        body["pan"] = g.pan(p)
    if r.random() < 0.4:
        body["dob"] = g.dob(p, 2)
    if r.random() < 0.4:
        body["salary"] = g.comp()
    jdump(b, body, indent=r.choice([None, 2]))
    b("\n")


@tpl("integration_debug", "int.curl")
def t_curl(g):
    b, r = g.b, g.rng
    p = g.subject(0.7)
    k = r.randrange(4)
    url = r.choice(["https://api.kestrelsys.in/v2/payroll/run", f"https://{TENANT_HOST}/ccx/api/v1/kestrel/workers", "https://hooks.kestrelsys.in/hr/onboarding"])
    if k == 0:
        b("curl -X POST '", url, "' \\\n  -H 'Authorization: Bearer ", g.secret("bearer"), "' \\\n  -H 'Content-Type: application/json' \\\n  -d '")
        jdump(b, {"employeeId": g.empid(p), "pan": g.pan(p), "account": g.acct(), "ifsc": g.ifsc()}, indent=None)
        b("'\n")
    elif k == 1:
        b("curl -u ", r.choice(["admin", "svc_hr", "isu_payroll"]), ":", g.secret("password"), " -X GET ", url, "/", g.empid(p), " -H 'Accept: application/json'\n")
    elif k == 2:
        b('curl --request POST --url ', url, ' --header "x-api-key: ', g.secret(r.choice(["sk", "sk_proj"])), '" --header "content-type: application/json" --data \'')
        jdump(b, {"email": g.email(p), "phone": g.phone(p), "name": nm(p)}, indent=None)
        b("'\n")
    else:
        b("curl -sS -H \"Authorization: token ", g.secret("ghp"), "\" https://api.github.com/repos/kestrelsys/hr-integrations/contents/config/prod.yml\n")


@tpl("integration_debug", "int.eib_error")
def t_eib(g):
    b, r = g.b, g.rng
    b(f"Integration Event: Load Employee Data (EIB) - {r.choice(['Completed with Errors', 'Failed'])}\nInitiated: {ts(g)} by ", nm(g.other(1.0)), "\n\n")
    for _ in range(r.randint(2, 4)):
        p = g.subject(0.7)
        row = r.randint(2, 900)
        k = r.randrange(4)
        b(f"Row {row}: ")
        if k == 0:
            b("Validation error: The value '", g.pan(p), "' is not valid for field National_ID_Number (Worker ", g.empid(p), ").\n")
        elif k == 1:
            b("Invalid Bank_Account_Number '", g.acct(), "' for routing '", g.ifsc(), "': length does not match bank.\n")
        elif k == 2:
            b("Date_of_Birth ", g.dob(p, r.choice([0, 1, 3])), " is not a valid date for ", nm(p), ": worker would be under minimum working age.\n")
        else:
            b("Base_Pay ", g.comp(), " exceeds the maximum of the compensation grade for ", g.empid(p), ".\n")


@tpl("integration_debug", "int.studio_fault")
def t_studio(g):
    b, r = g.b, g.rng
    p = g.subject(0.7)
    b('<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">\n  <SOAP-ENV:Header>\n    <wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">\n      <wsse:UsernameToken>\n')
    b("        <wsse:Username>", r.choice(["ISU_HR_Sync@kestrel", "ISU_Payroll@kestrel"]), "</wsse:Username>\n")
    b('        <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordText">', g.secret("password"), "</wsse:Password>\n")
    b("      </wsse:UsernameToken>\n    </wsse:Security>\n  </SOAP-ENV:Header>\n  <SOAP-ENV:Body>\n    <SOAP-ENV:Fault>\n      <faultcode>SOAP-ENV:Client.validationError</faultcode>\n")
    b("      <faultstring>Validation error occurred. Worker ", nm(p), " (", g.empid(p), ") - ", r.choice(["Email_Address", "Phone_Number"]), " ", r.choice([g.email(p), g.phone(p)]), " already in use</faultstring>\n")
    b("      <detail><wd:Validation_Fault xmlns:wd=\"urn:com.workday/bsvc\"><wd:Detail_Message>Invalid ID value.</wd:Detail_Message></wd:Validation_Fault></detail>\n    </SOAP-ENV:Fault>\n  </SOAP-ENV:Body>\n</SOAP-ENV:Envelope>\n")


@tpl("integration_debug", "int.env_file")
def t_env(g):
    b, r = g.b, g.rng
    b("# hr-sync service config\nNODE_ENV=production\nLOG_LEVEL=", r.choice(["info", "debug"]), "\nPORT=", str(r.randint(3000, 9000)), f"\nDB_HOST=10.{r.randint(0, 255)}.{r.randint(0, 255)}.{r.randint(2, 250)}\nDB_USER=hrsync\n")
    b("DB_PASSWORD=", g.secret("password"), "\n")
    for line in r.sample([
        lambda: b("AWS_ACCESS_KEY_ID=", g.secret("akia"), "\nAWS_SECRET_ACCESS_KEY=", g.secret("awssecret"), "\n"),
        lambda: b("OPENAI_API_KEY=", g.secret(r.choice(["sk", "sk_proj"])), "\n"),
        lambda: b("GITHUB_TOKEN=", g.secret("ghp"), "\n"),
        lambda: b("WORKDAY_CLIENT_SECRET=", g.secret("bearer"), "\n"),
        lambda: b("JWT_SIGNING_TOKEN=", g.secret("jwt"), "\n"),
        lambda: b("SMTP_PASSWORD=", g.secret("password"), "\nSMTP_USER=", g.mailbox(), "\n"),
    ], r.randint(1, 3)):
        line()
    b("FEATURE_FLAGS=bank_verify,pan_check\nSENTRY_DSN=https://", g.hexs(32), "@o", str(r.randint(100000, 999999)), ".ingest.sentry.io/", str(r.randint(1000000, 9999999)), "\n")


@tpl("integration_debug", "int.yaml_config")
def t_yaml(g):
    b, r = g.b, g.rng
    b("spring:\n  datasource:\n    url: jdbc:postgresql://10.", str(r.randint(0, 255)), ".", str(r.randint(0, 255)), ".", str(r.randint(2, 250)), ":5432/hrms\n    username: hrms_app\n    password: ", g.secret("password"), "\n")
    b("integration:\n  workday:\n    tenant: kestrel\n    client_id: ", g.hexs(24), "\n    client_secret: ", g.secret("bearer"), "\n")
    b("  sftp:\n    host: sftp.kestrelsys.in\n    user: payroll_bot\n    password: ", g.secret("password"), "\n")
    if r.random() < 0.5:
        b("  notify:\n    to: ", g.email(g.roster_person()), "\n    from: ", g.mailbox(), "\n")


@tpl("integration_debug", "int.jwt_log")
def t_jwt_log(g):
    b, r = g.b, g.rng
    p = g.roster_person()
    claims = {"sub": p.emp_id, "email": p.email, "name": p.full, "iat": 1780000000 + r.randint(0, 9999999), "exp": 1780003600 + r.randint(0, 9999999)}
    tok = g.jwt(claims)
    b(ts(g), " INFO  auth.filter - incoming request from ", g.ip(), " token=", E(tok, "SECRET"), "\n")
    b(ts(g), " DEBUG auth.filter - decoded claims sub=", E(p.emp_id, "EMPLOYEE_ID"), " email=", g.email(p), " name=", nm(p), "\n")


@tpl("integration_debug", "int.sftp_payroll_log")
def t_sftp(g):
    b, r = g.b, g.rng
    d = g.rand_date(2026, 2026).strftime("%Y%m%d")
    b(ts(g), f" INFO  sftp.upload - connecting to sftp.kestrelsys.in:22 as payroll_bot\n", ts(g), f" INFO  sftp.upload - uploading BANK_ADVICE_{d}.csv ({r.randint(40, 900)} KB)\n")
    for _ in range(r.randint(2, 4)):
        p = g.subject(0.7)
        b(ts(g), " WARN  bank.advice - row rejected: name=", nm(p), " acct=", g.acct(), " ifsc=", g.ifsc(), " amt=", g.comp(monthly=True), "\n")
    b(ts(g), f" INFO  sftp.upload - done, {r.randint(1, 30)} rows rejected\n")


@tpl("integration_debug", "int.escaped_json_log")
def t_escaped(g):
    b, r = g.b, g.rng
    p = g.subject(0.7)
    inner = Builder()
    body = {"employeeId": g.empid(p), "fullName": nm(p), "email": g.email(p), "phone": g.phone(p)}
    if r.random() < 0.5:
        body["bankAccount"] = g.acct()
        body["ifsc"] = g.ifsc()
    if r.random() < 0.4:
        body["birthDate"] = g.dob(p, 2)
    jdump(inner, body, indent=None)
    b(ts(g), f" ERROR [{r.choice(['worker-1', 'consumer-3', 'http-7'])}] c.k.hr.EventPublisher - failed to publish, payload=\"")
    b.add_mapped(inner, json_esc)
    b("\" cause=", r.choice(["TimeoutException", "ConnectException", "KafkaException"]), "\n")


@tpl("integration_debug", "int.kv_syslog")
def t_kv(g):
    b, r = g.b, g.rng
    for _ in range(r.randint(2, 4)):
        p = g.subject(0.7)
        b(ts(g), f" hrsvc[{r.randint(1000, 60000)}]: event=", r.choice(["profile_update", "kyc_check", "bank_change"]), " emp=", g.empid(p), " user=", g.email(p))
        if r.random() < 0.6:
            b(" mobile=", g.phone(p))
        if r.random() < 0.5:
            b(" pan=", g.pan(p))
        if r.random() < 0.3:
            b(" upi=", g.upi(p))
        b(f" ip={g.ip()} status={r.choice([200, 200, 400, 422])} dur_ms={r.randint(5, 900)}\n")


@tpl("integration_debug", "int.python_snippet")
def t_py_snip(g):
    b, r = g.b, g.rng
    p = g.subject(0.7)
    b("import requests\n\nBASE = \"https://api.kestrelsys.in/v2\"\nheaders = {\n    \"Authorization\": \"Bearer ", g.secret("bearer"), "\",\n    \"Content-Type\": \"application/json\",\n}\n\n")
    b("def test_update():\n    payload = ")
    jdump(b, {"employee_id": g.empid(p), "name": nm(p), "card": g.card()} if r.random() < 0.3 else {"employee_id": g.empid(p), "name": nm(p), "upi": g.upi(p), "phone": g.phone(p)}, indent=None)
    b("\n    r = requests.post(f\"{BASE}/employees\", json=payload, headers=headers)\n    assert r.status_code == 200\n")
    if r.random() < 0.5:
        b("\nDB_URL = \"postgresql://hrms:", g.secret("password"), "@10.0.4.12:5432/hrms\"\n")


@tpl("integration_debug", "int.payment_gateway_log")
def t_payment(g):
    b, r = g.b, g.rng
    for _ in range(r.randint(2, 3)):
        p = g.subject(0.7)
        b(ts(g), " [payments] ", r.choice(["AUTH", "CAPTURE", "REFUND"]), f" txn={g.hexs(16)} ref={g.order12()} card=", g.card(), " exp=", f"{r.randint(1, 12):02d}/{r.randint(27, 32)}", " holder=", nm(p), " amount=", str(r.randint(100, 90000)), ".00 INR\n")
    b(ts(g), " [payments] gateway_key=", g.secret("sk"), "\n")


@tpl("integration_debug", "int.git_diff_leak")
def t_git(g):
    b, r = g.b, g.rng
    b("diff --git a/deploy/values-prod.yaml b/deploy/values-prod.yaml\nindex ", g.hexs(7), "..", g.hexs(7), " 100644\n--- a/deploy/values-prod.yaml\n+++ b/deploy/values-prod.yaml\n@@ -12,6 +12,9 @@ hrsync:\n")
    b("   replicas: 2\n+  awsAccessKeyId: ", g.secret("akia"), "\n+  awsSecretAccessKey: ", g.secret("awssecret"), "\n+  adminPassword: ", g.secret("password"), "\n")
    if r.random() < 0.5:
        b("+  notifyEmail: ", g.email(g.roster_person()), "\n")
    b("   image: registry.kestrelsys.in/hrsync:", f"{r.randint(1, 4)}.{r.randint(0, 20)}.{r.randint(0, 9)}", "\n")


# --------------------------------------------------------------------------- negatives (no gold spans)
COMPANIES = ["Infosys", "Tata Consultancy Services", "Wipro", "HCLTech", "Reliance Industries", "Hindustan Unilever", "Larsen & Toubro",
             "Mahindra & Mahindra", "Bharti Airtel", "State Bank of India", "HDFC Bank", "ICICI Bank", "Asian Paints", "Maruti Suzuki",
             "Amazon Web Services", "Microsoft", "SAP", "Oracle", "Workday", "Google", "Accenture", "Deloitte"]
ITEMS = ["laptop docking station", "annual software licence", "ergonomic chair", "cloud hosting (monthly)", "conference passes",
         "cafeteria coupons", "printer toner", "training vouchers", "security audit", "courier charges"]


def _neg_amount(g):
    r = g.rng
    a = r.choice([r.randrange(199, 9999, 100), r.randrange(10000, 900000, 500), r.randrange(100000, 90000000, 1000)])
    return r.choice([f"₹{inr_group(a)}", f"Rs. {inr_group(a)}", f"INR {inr_group(a)}", f"{inr_group(a)}"])


@tpl("negatives", "neg.invoice")
def n_invoice(g):
    b, r = g.b, g.rng
    v = r.choice(COMPANIES)
    b(f"TAX INVOICE\nInvoice No: INV-{r.randint(2024, 2026)}-{r.randint(1, 999999):06d}   Date: {g.date_txt()}\nVendor: {v}   GSTIN: {r.randint(10, 37)}{g._rand(string.ascii_uppercase, 5)}{g._digits(4)}{g._rand(string.ascii_uppercase, 1)}1Z{g._rand(string.digits, 1)}\n")
    b(f"PO Ref: 45{g._digits(8)}   Order ID: {g.order12()}\n")
    for i in range(r.randint(2, 4)):
        b(f"{i + 1}. {r.choice(ITEMS)}   qty {r.randint(1, 40)}   {_neg_amount(g)}\n")
    b(f"Total (incl. GST 18%): {_neg_amount(g)}\nPayment terms: net {r.choice([15, 30, 45, 60])} days. Remit to the account listed in the vendor master.\n")


@tpl("negatives", "neg.order_ids")
def n_orders(g):
    b, r = g.b, g.rng
    b("Order status report\n")
    for _ in range(r.randint(4, 8)):
        b(f"order_id={g.order12()} sku=SKU-{g._rand(string.ascii_uppercase, 3)}-{r.randint(1000, 9999)} status={r.choice(['SHIPPED', 'PENDING', 'DELIVERED', 'RETURNED'])} awb={g._digits(r.choice([12, 14, 16]))} value={_neg_amount(g)}\n")
    b(f"Customer helpdesk hours: 9-6 IST. Batch {g.hexs(8)} processed at {ts(g)}.\n")


@tpl("negatives", "neg.it_ticket")
def n_ticket(g):
    b, r = g.b, g.rng
    b(f"INC{r.randint(1000000, 9999999):07d} | P{r.randint(1, 4)} | {r.choice(['Open', 'In Progress', 'Resolved'])}\n")
    b(f"Short description: {r.choice(['VPN drops every 20 minutes', 'Payroll job failing at step 4', 'Laptop not booting after update', 'SSO redirect loop on HR portal'])}\n")
    b(f"Affected host: app-{r.randint(1, 40):02d}.kestrelsys.in ({g.ip()}) build {r.randint(1, 9)}.{r.randint(0, 30)}.{r.randint(0, 99)}\n")
    b(f"Related: HR-{r.randint(1000, 9999)}, CHG{r.randint(100000, 999999)}, JIRA PAY-{r.randint(100, 9999)}\nWork notes: restarted service at {ts(g)}; monitoring. Last seen ip {g.ip()}.\n")


@tpl("negatives", "neg.policy_text")
def n_policy(g):
    b, r = g.b, g.rng
    b(f"HR-POL-{r.randint(10, 99)} Leave and Expense Policy (v{r.randint(1, 5)}.{r.randint(0, 9)}), effective {g.date_txt()}\n\n")
    b(f"1. Employees are entitled to {r.choice([18, 20, 24])} days of earned leave per calendar year, accrued monthly.\n")
    b(f"2. Broadband reimbursement is capped at {_neg_amount(g)} per month against a valid bill.\n")
    b(f"3. Client entertainment above {_neg_amount(g)} requires prior approval from the department head.\n")
    b(f"4. Claims must be submitted within {r.choice([30, 45, 60])} days of the expense date; late claims are not processed.\n")
    b("5. Compensation decisions are made by the review committee and are not discussed in this policy.\n")


@tpl("negatives", "neg.gl_codes")
def n_gl(g):
    b, r = g.b, g.rng
    b("Journal upload JV-", str(r.randint(100000, 999999)), "\nPeriod: ", r.choice(_MONTHS), " ", str(r.choice([2025, 2026])), "\n")
    for _ in range(r.randint(3, 6)):
        b(f"GL {r.randint(1000, 9999)}-{r.randint(1000, 9999)}-{r.randint(1, 99):02d}  CC-{r.randint(1000, 9999)}  WBS P-{r.randint(10000, 99999)}-{r.randint(1, 20):02d}  DR {inr_group(r.randrange(1000, 9000000, 50))}.00\n")
    b(f"Control total: {inr_group(r.randrange(100000, 90000000, 50))}.00 | Prepared by finance-ops | Batch {g.uuid()}\n")


@tpl("negatives", "neg.release_notes")
def n_release(g):
    b, r = g.b, g.rng
    v = f"{r.randint(1, 5)}.{r.randint(0, 30)}.{r.randint(0, 20)}"
    b(f"## hrsync {v} ({g.rand_date().isoformat()})\n\n- upgrade requests to {r.randint(2, 3)}.{r.randint(0, 31)}.{r.randint(0, 9)}\n")
    b(f"- fix retry storm against {g.ip()} (commit {g.hexs(40)})\n- bump base image sha256:{g.hexs(64)}\n- new health endpoint /v{r.randint(1, 3)}/health returns build {r.randint(1000, 99999)}\n")
    b(f"- trace ids are now UUIDv4 e.g. {g.uuid()}\n- Known issue: DNS lookups for 10.{r.randint(0, 255)}.0.{r.randint(1, 254)} time out on node pool {r.randint(1, 9)}\n")


@tpl("negatives", "neg.product_catalog")
def n_catalog(g):
    b, r = g.b, g.rng
    b("Procurement catalogue extract\n")
    for _ in range(r.randint(4, 7)):
        b(f"{r.choice(ITEMS).title()} | SKU {g._rand(string.ascii_uppercase, 2)}-{g._digits(6)} | EAN {r.choice(['890', '978'])}{g._digits(10)} | {_neg_amount(g)} | GST {r.choice([5, 12, 18, 28])}%\n")
    b(f"ISBN-13 978-{r.randint(0, 9)}-{g._digits(4)}-{g._digits(4)}-{r.randint(0, 9)} (training handbook)\n")


@tpl("negatives", "neg.news_public")
def n_news(g):
    b, r = g.b, g.rng
    c1, c2 = r.sample(COMPANIES, 2)
    b(f"{c1} reported quarterly revenue of Rs. {inr_group(r.randint(10000, 99999))} crore, up {r.randint(2, 19)}.{r.randint(0, 9)}% year on year. {c2} announced a hiring freeze in two geographies.\n")
    b(f"Analysts at {r.choice(COMPANIES)} expect margins near {r.randint(15, 28)}.{r.randint(0, 9)}% by {r.choice(['Q3', 'Q4'])} FY{r.randint(26, 28)}. Headcount stood at {inr_group(r.randint(20000, 400000))} as of {g.date_txt()}.\n")
    b(f"Shares of {c1} closed at Rs. {inr_group(r.randint(400, 4500))}.{r.randint(10, 99)} on the NSE, ticker volume {inr_group(r.randint(100000, 9000000))}.\n")


@tpl("negatives", "neg.meeting_invite")
def n_meeting(g):
    b, r = g.b, g.rng
    b(f"Subject: {r.choice(['Payroll cutover sync', 'Quarterly business review', 'Benefits vendor call', 'Audit kickoff'])}\nWhen: {g.date_txt()} {r.randint(9, 17)}:{r.choice(['00', '30'])} IST\n")
    b(f"Join Zoom Meeting: https://kestrelsys.zoom.us/j/{g._digits(11)}\nMeeting ID: {g._digits(3)} {g._digits(4)} {g._digits(4)}\nPasscode: (see calendar)\nRoom: Tower {r.choice('ABC')}, Floor {r.randint(1, 14)}, Room {r.randint(101, 1499)}\n")
    b("Agenda: review open items, confirm cutoff of ", g.date_txt(), ", and decide on the rollout plan.\n")


@tpl("negatives", "neg.log_hashes")
def n_log(g):
    b, r = g.b, g.rng
    for _ in range(r.randint(4, 7)):
        b(ts(g), f" INFO  req={g.uuid()} path=/v2/{r.choice(['workers', 'payslips', 'leave', 'orgs'])} status={r.choice([200, 200, 204, 404])} etag=\"{g.hexs(32)}\" sha256={g.hexs(64)} session={g._rand(_ALNUM, 26)} bytes={r.randint(100, 90000)}\n")
    b(f"checksum file: build-{r.randint(1000, 9999)}.tar.gz  md5 {g.hexs(32)}\n")


@tpl("negatives", "neg.dates_generic")
def n_dates(g):
    b, r = g.b, g.rng
    y0 = r.randint(24, 27)
    b(f"Payroll calendar FY{y0}-{y0 + 1}\n")
    for _ in range(r.randint(4, 7)):
        b(f"- {r.choice(['Attendance cut-off', 'Payroll freeze', 'Salary credit', 'Investment declaration deadline', 'Form 16 release'])}: {g.date_txt()}\n")
    b(f"Note: all dates are in {r.choice(['DD/MM/YYYY', 'YYYY-MM-DD'])} format; the company was incorporated on {g.date_txt(0)} and the office moved on {g.date_txt(3)}.\n")


@tpl("negatives", "neg.finance_budget")
def n_budget(g):
    b, r = g.b, g.rng
    b(f"Q{r.randint(1, 4)} budget review - {r.choice(DEPTS)}\n")
    for line in ["Travel", "Recruitment agency fees", "Cloud infrastructure", "Training", "Office supplies", "Marketing events"]:
        if r.random() < 0.7:
            b(f"{line}: budget {_neg_amount(g)}, actual {_neg_amount(g)}, variance {r.randint(-40, 40)}%\n")
    b(f"Approved headcount plan: {r.randint(20, 400)} (open reqs {r.randint(1, 30)}). Total spend {_neg_amount(g)}.\n")


@tpl("negatives", "neg.template_placeholders")
def n_placeholders(g):
    b, r = g.b, g.rng
    b("Dear {{employee_name}},\n\nYour revised CTC of {{new_ctc}} is effective {{effective_date}}. Employee ID: {{emp_id}}. Please verify your details:\n")
    b("PAN: XXXXX0000X   Aadhaar: XXXX XXXX 0000   Bank a/c: ************", g._digits(4), "   Mobile: +91 XXXXX XXXXX\n")
    b("DOB: DD/MM/YYYY\nEmail: <employee_email>\n\nRegards,\n{{hrbp_name}}\n")


@tpl("negatives", "neg.luhn_lookalikes")
def n_luhn(g):
    b, r = g.b, g.rng
    b("Warehouse scan log\n")
    for _ in range(r.randint(3, 6)):
        body = g._digits(14)
        n = body + luhn_check_digit(body) if r.random() < 0.25 else body + g._digits(1)
        b(f"IMEI {n[:15]}  tracking {g._digits(16)}  pallet {g._digits(18)}  {r.choice(['scanned', 'dispatched', 'received'])} {ts(g)}\n")
    b(f"Manifest {g.uuid()} contains {r.randint(20, 900)} units.\n")


@tpl("negatives", "neg.at_signs")
def n_at(g):
    b, r = g.b, g.rng
    b(f"@channel standup moved to {r.randint(9, 11)}:{r.choice(['00', '15', '30'])}. Use the @oncall alias for prod issues.\n")
    b(f"Assets: logo@2x.png, hero@3x.webp, icon@{r.randint(1, 3)}x.svg. Packages: react@18.2.0, lodash@4.17.21, @types/node@20.11.5\n")
    b(f"Decorator usage: @app.route('/health'), @retry(times={r.randint(2, 6)}). Git remote: origin -> ssh://git.kestrelsys.in/hr/sync.git branch release/{r.randint(1, 9)}.{r.randint(0, 9)}\n")


@tpl("negatives", "neg.hr_generic_text")
def n_hr_generic(g):
    b, r = g.b, g.rng
    b(f"Agenda - HR leadership sync, {g.date_txt()}\n\n1. Attrition trend: {r.randint(8, 22)}.{r.randint(0, 9)}% annualised, vs industry {r.randint(10, 20)}%.\n")
    b(f"2. Merit cycle: overall budget {r.randint(6, 12)}% of payroll; guidelines to be published by {g.date_txt()}.\n3. Engagement survey response rate {r.randint(60, 95)}%.\n")
    b("4. Policy refresh: leave encashment, notice period buyout, and the referral programme.\n5. Any other business.\n")


# --------------------------------------------------------------------------- extra card-bearing templates
@tpl("hr_free_text", "hr.lost_card_report")
def t_lost_card(g):
    b, r = g.b, g.rng
    emp, fin = g.subject(0.7), g.other(1.0)
    b("Subject: URGENT - corporate card lost\n\n", g.ref(fin, "first"), ", I lost my wallet at ", r.choice(["the airport", "Koramangala", "Andheri station", "the client site"]), " this morning. ")
    b("Please block the card immediately - number is ", g.card(), " (issued in ", "my name", "). ")
    b("Also my personal debit card ", g.card(), " was in there. Employee code ", g.empid(emp), ", call me on ", g.phone(emp), ".\n\n", g.ref(emp, "full"), "\n")


@tpl("hris_structured", "hris.expense_cards_json")
def t_exp_cards(g):
    b, r = g.b, g.rng
    p = g.subject(0.7)
    cards = [{"cardNumber": g.card(), "network": r.choice(["VISA", "MASTERCARD", "RUPAY", "AMEX"]), "expiry": f"{r.randint(1, 12):02d}/{r.randint(26, 32)}",
              "limit": {"amount": r.randrange(50000, 1000000, 25000), "currency": "INR"}, "status": r.choice(["ACTIVE", "BLOCKED"])} for _ in range(r.randint(1, 2))]
    jdump(b, {"employee": {"id": g.empid(p), "name": nm(p)}, "expenseCards": cards}, indent=r.choice([2, None]))
    b("\n")


@tpl("integration_debug", "int.card_vault_error")
def t_card_vault(g):
    b, r = g.b, g.rng
    p = g.subject(0.7)
    b(ts(g), " ERROR [card-vault] tokenization failed: PAN=", g.card(), " emp=", g.empid(p), f" reason={r.choice(['BIN_NOT_SUPPORTED', 'LUHN_OK_BUT_DECLINED', 'TIMEOUT'])}\n")
    b(ts(g), " DEBUG [card-vault] request body: ")
    jdump(b, {"holder": nm(p), "card": g.card(), "email": g.email(p)}, indent=None)
    b("\n")


@tpl("hr_free_text", "hr.travel_booking_chat")
def t_travel(g):
    b, r = g.b, g.rng
    a, c = g.subject(0.7), g.other()
    b("[", f"{r.randint(9, 18)}:{r.randint(10, 59)}", "] ", g.ref(c, "lower_first"), ": ", g.ref(a, "first"), " for the ", r.choice(["Delhi", "Singapore", "Frankfurt"]), " trip pls send the card details for booking\n")
    b("[", f"{r.randint(9, 18)}:{r.randint(10, 59)}", "] ", g.ref(a, "lower_first"), ": use ", g.card(), " exp ", f"{r.randint(1, 12):02d}/{r.randint(27, 31)}", ", or upi ", g.upi(a), " if it fails\n")


# --------------------------------------------------------------------------- orchestration
SLICE_ABBR = {"hr_free_text": "hr", "hris_structured": "hris", "integration_debug": "int", "negatives": "neg"}
ROSTER_TARGET = 0.70       # share of PERSON mentions drawn from the roster (rest: external)
REQUIRED_COVERAGE = 2      # every split needs >= this many templates emitting each positive type


def _probe_types(seed, pools, people):
    """Which entity types can each template emit?  (union over 40 dry runs on the dev pool)."""
    out = {}
    for sl, tid, fn in TEMPLATES:
        seen = set()
        for i in range(40):
            g = Gen(random.Random(f"{seed}|probe|{tid}|{i}"), "dev", people["dev"], pools["dev"])
            fn(g)
            _, spans, _, _ = g.finish()
            seen |= {s["type"] for s in spans}
        out[tid] = seen
    return out


def assign_templates(seed, ttypes):
    """Split templates 60/20/20 per slice, retrying until each split covers every type >= 2 times."""
    r = random.Random(f"{seed}|assign")
    by_slice = {sl: sorted(t for s, t, _ in TEMPLATES if s == sl) for sl in SLICES}
    for _ in range(5000):
        assign = {}
        for sl in SLICES:
            ids = list(by_slice[sl])
            r.shuffle(ids)
            n = len(ids)
            n_test = max(2, round(n * 0.2))
            n_tune = max(2, round(n * 0.2))
            for i, tid in enumerate(ids):
                assign[tid] = "test" if i < n_test else ("tune" if i < n_test + n_tune else "dev")
        ok = True
        for s in SPLITS:
            for ty in TYPES:
                if sum(1 for t, sp in assign.items() if sp == s and ty in ttypes[t]) < REQUIRED_COVERAGE:
                    ok = False
                    break
            if not ok:
                break
        if ok:
            return assign
    raise RuntimeError("could not find a template split covering all types in every split")


def generate_items(seed, pools, people, assign, ttypes):
    fns = {tid: (sl, fn) for sl, tid, fn in TEMPLATES}
    stats = {"person_mentions": {s: Counter() for s in SPLITS}}
    result = {}
    for split in SPLITS:
        r = random.Random(f"{seed}|{split}|plan")
        tpls = {sl: sorted(t for t, sp in assign.items() if sp == split and fns[t][0] == sl) for sl in SLICES}
        items, counters, tally = [], Counter(), Counter()
        seq = Counter()

        def emit(tid):
            sl, fn = fns[tid]
            k = seq[tid]
            seq[tid] += 1
            tot = sum(stats["person_mentions"][split].values())
            share = stats["person_mentions"][split]["roster"] / tot if tot >= 60 else ROSTER_TARGET
            adj = max(-0.4, min(0.3, 3.0 * (ROSTER_TARGET - share)))
            g = Gen(random.Random(f"{seed}|{split}|{tid}|{k}"), split, people[split], pools[split], adj)
            fn(g)
            text, spans, ns, srcs = g.finish()
            counters[sl] += 1
            for s in srcs:
                stats["person_mentions"][split][s] += 1
            for sp in spans:
                tally[sp["type"]] += 1
            items.append({"id": f"{split}-{SLICE_ABBR[sl]}-{counters[sl]:05d}", "slice": sl, "template_id": tid,
                          "name_source": ns, "text": text, "spans": spans})

        mult = {"dev": 3, "tune": 1, "test": 1}[split]
        for sl in SLICES:
            for _ in range(BASE_COUNTS[sl] * mult):
                emit(r.choice(tpls[sl]))
        # top-up so that every type meets its (scaled) minimum
        mins = {t: TEST_MIN[t] * SPLIT_MULT[split] for t in TYPES}
        guard = 0
        while True:
            deficits = [t for t in TYPES if tally[t] < mins[t]]
            if not deficits:
                break
            guard += 1
            if guard > 20000:
                raise RuntimeError(f"cannot reach minimum for {deficits} in {split}")
            t = max(deficits, key=lambda x: mins[x] - tally[x])
            cands = [tid for sl in SLICES if sl != "negatives" for tid in tpls[sl] if t in ttypes[tid]]
            emit(r.choice(cands))
        random.Random(f"{seed}|{split}|shuffle").shuffle(items)
        result[split] = items
    return result, stats


def _dump_jsonl(path, rows):
    data = "".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n" for x in rows).encode("utf-8")
    with open(path, "wb") as f:
        f.write(data)
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "lines": len(rows)}


def generate(out_dir, seed=1337):
    os.makedirs(out_dir, exist_ok=True)
    pools = build_name_pools(seed)
    people = build_roster(seed, pools)
    ttypes = _probe_types(seed, pools, people)
    assign = assign_templates(seed, ttypes)
    data, stats = generate_items(seed, pools, people, assign, ttypes)

    files = {"roster.jsonl": _dump_jsonl(os.path.join(out_dir, "roster.jsonl"), roster_rows(people))}
    counts = {}
    n_items = {}
    for split in SPLITS:
        files[f"{split}.jsonl"] = _dump_jsonl(os.path.join(out_dir, f"{split}.jsonl"), data[split])
        counts[split] = {}
        n_items[split] = {}
        for sl in SLICES:
            its = [i for i in data[split] if i["slice"] == sl]
            n_items[split][sl] = len(its)
            c = Counter(sp["type"] for i in its for sp in i["spans"])
            counts[split][sl] = {t: c.get(t, 0) for t in TYPES}
        counts[split]["ALL"] = {t: sum(counts[split][sl][t] for sl in SLICES) for t in TYPES}
    manifest = {
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "splits": list(SPLITS),
        "roster_size": sum(len(v) for v in people.values()),
        "items": n_items,
        "span_counts": counts,
        "person_mentions_by_name_source": {s: dict(stats["person_mentions"][s]) for s in SPLITS},
        "template_split": {s: sorted(t for t, sp in assign.items() if sp == s) for s in SPLITS},
        "files": files,
    }
    with open(os.path.join(out_dir, "MANIFEST.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="eval/data")
    ap.add_argument("--seed", type=int, default=1337)
    a = ap.parse_args(argv)
    m = generate(a.out, a.seed)
    tot = {s: sum(m["items"][s].values()) for s in SPLITS}
    print(f"wrote {a.out}: items {tot}, roster {m['roster_size']}, seed {a.seed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
