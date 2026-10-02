# E-UTIL review cases

Items with the largest RAW-vs-protected difference (plus at least one per task type). For human review; scores are deterministic. 'What left' is the outbound prompt.

## classification-01  (classification, purpose ticket_classification, format label)

Difference score: 2.000

### Instruction

~~~~
Classify this helpdesk ticket into exactly one category.
~~~~

### RAW answer

~~~~
payroll
~~~~

### GATEWAY prompt (what left)

BLOCKED: BANK_ACCOUNT

### GATEWAY restored answer

~~~~
(none)
~~~~

### MAX prompt (what left)

~~~~
Classify this helpdesk ticket into exactly one category.

---
From: [EMAIL_51X1H8]
Subject: Salary not credited
Hi, my September salary has not come into my account. I had changed my bank last month, new account [BANK_ACCOUNT_5QT5Q7], [VALUE_1] [IFSC_910YWV]. Kindly check with payroll. Employee ID [EMPLOYEE_ID_0GX8ED].

Answer with exactly one label from: payroll, leave, benefits, onboarding, exit, grievance, it_access, compliance. Output the label only.
~~~~

### MAX restored answer

~~~~
payroll
~~~~

### Scores

| condition | correct | score | entity pres. | fact. equiv. | sections | crit masked | unrestored | fabricated | forbidden | blocked |
|---|---|---|---|---|---|---|---|---|---|---|
| raw | True | 1.0 | n/a | n/a | n/a | 0 | 0 | 0 | 0 | False |
| gateway | False | 0.0 | n/a | n/a | n/a | 0 | 0 | 0 | 0 | True |
| max | True | 1.0 | n/a | n/a | n/a | 0 | 0 | 0 | 0 | False |

## classification-02  (classification, purpose ticket_classification, format label)

Difference score: 2.000

### Instruction

~~~~
Classify this helpdesk ticket into exactly one category.
~~~~

### RAW answer

~~~~
it_access
~~~~

### GATEWAY prompt (what left)

BLOCKED: SECRET

### GATEWAY restored answer

~~~~
(none)
~~~~

### MAX prompt (what left)

~~~~
Classify this helpdesk ticket into exactly one category.

---
Ticket from [PERSON_08WZMP] ([EMPLOYEE_ID_6YHPR2]): I am locked out of the [VALUE_1] portal after [VALUE_2] wrong attempts. Please unlock. Current password is [SECRET_084SQQ], user id gnaik. Thanks.

Answer with exactly one label from: payroll, leave, benefits, onboarding, exit, grievance, it_access, compliance. Output the label only.
~~~~

### MAX restored answer

~~~~
it_access
~~~~

### Scores

| condition | correct | score | entity pres. | fact. equiv. | sections | crit masked | unrestored | fabricated | forbidden | blocked |
|---|---|---|---|---|---|---|---|---|---|---|
| raw | True | 1.0 | n/a | n/a | n/a | 0 | 0 | 0 | 0 | False |
| gateway | False | 0.0 | n/a | n/a | n/a | 0 | 0 | 0 | 0 | True |
| max | True | 1.0 | n/a | n/a | n/a | 1 | 0 | 0 | 0 | False |

## email_drafting-01  (email_drafting, purpose email_draft, format text)

Difference score: 2.000

### Instruction

~~~~
Draft an email to the candidate with the revised CTC, joining date and reply-by date. Sign off as Meera Krishnamurthy, HR Business Partner.
~~~~

### RAW answer

~~~~
Subject: Revised Offer - Senior Analyst, Finance Systems (L4)

Dear Nandini,

Thank you for your time and interest in the Senior Analyst, Finance Systems role. We are pleased to share a revised offer for you.

Revised CTC: Rs 19,50,000 per annum (previously Rs 18,50,000)
Role and grade: Senior Analyst, Finance Systems, L4
Proposed date of joining: 4 January 2027

Kindly confirm your acceptance by replying to this email on or before 12 October 2026. If you have any questions, please feel free to reach out to me.

We look forward to welcoming you to the team.

Warm regards,
Meera Krishnamurthy
HR Business Partner
~~~~

### GATEWAY prompt (what left)

BLOCKED: PAN

### GATEWAY restored answer

~~~~
(none)
~~~~

### MAX prompt (what left)

~~~~
Draft an email to the candidate with the revised CTC, joining date and reply-by date. Sign off as [PERSON_01DT04], HR [VALUE_1] [VALUE_2].

---
{
 "candidate": "[PERSON_89B4RJ]",
 "email": "[EMAIL_2M6M6T]",
 "phone": "[PHONE_17S9SE]",
 "pan": "[PAN_65ABD0]",
 "date_of_birth": "[DATE_OF_BIRTH_3VEPVB]",
 "role": "Senior [VALUE_3], [VALUE_4] [VALUE_5]",
 "grade": "[VALUE_6]",
 "original_offer_ctc": "[COMPENSATION_20EZ2A]",
 "revised_offer_ctc": "[COMPENSATION_0ZGDQZ]",
 "joining_date": "[VALUE_7] January [VALUE_8]",
 "reply_by": "[VALUE_9] October [VALUE_10]"
}
~~~~

### MAX restored answer

~~~~
Subject: Revised Offer - Senior Analyst, Finance Systems

Dear Nandini Venkataraman,

Thank you for your continued interest in joining us. We are pleased to share a revised offer for the position of Senior Analyst, Finance Systems, Grade L4.

- Revised CTC: Rs 19,50,000 (earlier offer: Rs 18,50,000)
- Joining date: 4 January 2027
- Please reply by: 12 October 2026

Please confirm your acceptance by replying to this email on or before the date above. If you have any questions, I would be happy to help.

Warm regards,
Meera Krishnamurthy
HR Business Partner
~~~~

### Scores

| condition | correct | score | entity pres. | fact. equiv. | sections | crit masked | unrestored | fabricated | forbidden | blocked |
|---|---|---|---|---|---|---|---|---|---|---|
| raw | True | 1.0 | 0.8 | n/a | 1.0 | 0 | 0 | 0 | 0 | False |
| gateway | False | 0.0 | 0.0 | n/a | 0.0 | 0 | 0 | 0 | 0 | True |
| max | True | 1.0 | 1.0 | n/a | 1.0 | 4 | 0 | 0 | 0 | False |

## email_drafting-03  (email_drafting, purpose email_draft, format text)

Difference score: 2.000

### Instruction

~~~~
Draft an endorsement request email to the TPA to add Rajesh Kumar Gupta's newborn daughter to his group mediclaim cover. Include only what the TPA needs.
~~~~

### RAW answer

~~~~
To: endorsements@medisure-tpa.in
Subject: Endorsement Request - Addition of Newborn Dependant - Policy GMC-KD-2026-0457

Dear Medisure TPA Team,

We request you to add the newborn daughter of one of our employees to the Group Mediclaim policy by endorsement.

Policy number: GMC-KD-2026-0457
Employee: Rajesh Kumar Gupta (Employee ID: KD-10656)
Dependant to be added: Aarohi Gupta
Relation: Daughter
Date of birth: 21 September 2026

As per our policy rule, a newborn must be added within 30 days of birth, so please process this endorsement on or before 21 October 2026. Kindly confirm once the endorsement is done and share the updated member details or e-card.

If you need any further information, please let us know.

Regards,
HR Team
~~~~

### GATEWAY prompt (what left)

BLOCKED: AADHAAR, PAN

### GATEWAY restored answer

~~~~
(none)
~~~~

### MAX prompt (what left)

~~~~
Draft an endorsement request email to the [VALUE_1] to add [PERSON_77CXH0]'s newborn daughter to his group mediclaim cover. Include only what the [VALUE_1] needs.

---
HRIS dependant request
employee_id,full_name,pan,aadhaar,spouse,new_dependant,dependant_dob,relation
[EMPLOYEE_ID_7HNGRP],[PERSON_77CXH0],[PAN_0C6HMC],[AADHAAR_2PB8JC],[PERSON_2Q5005],[VALUE_2] [PERSON_4ZEEG2],[DATE_OF_BIRTH_2M8WPX],[VALUE_3]
Group [VALUE_4] policy number: [VALUE_5] (TPA: Medisure [VALUE_1], endorsements mailbox [EMAIL_5N17HM]).
Rule: newborn must be added by endorsement within [VALUE_6] days of birth.
~~~~

### MAX restored answer

~~~~
Subject: Endorsement request - addition of newborn dependant, policy GMC-KD-2026-0457

Dear Medisure TPA team,

We request you to add a newborn dependant to the group Mediclaim policy, number GMC-KD-2026-0457, by endorsement.

Employee: Rajesh Kumar Gupta, employee ID KD-10656
New dependant: Aarohi Gupta (relation: Daughter)
Date of birth: 21 September 2026

As per the rule, a newborn must be added within 30 days of birth, so we would appreciate it if the endorsement is processed within that window. Please confirm once completed and share the updated e-card or endorsement copy. Let us know if any further documents are required.

Regards,
HR Benefits Team
~~~~

### Scores

| condition | correct | score | entity pres. | fact. equiv. | sections | crit masked | unrestored | fabricated | forbidden | blocked |
|---|---|---|---|---|---|---|---|---|---|---|
| raw | True | 1.0 | 1.0 | n/a | 1.0 | 0 | 0 | 0 | 0 | False |
| gateway | False | 0.0 | 0.0 | n/a | 0.0 | 0 | 0 | 0 | 0 | True |
| max | True | 1.0 | 1.0 | n/a | 1.0 | 4 | 0 | 0 | 0 | False |

## extraction-01  (extraction, purpose hr_case_summary, format json)

Difference score: 2.000

### Instruction

~~~~
Extract the bank-change request as JSON with keys employee_id, full_name, account_number, ifsc, effective_month.
~~~~

### RAW answer

~~~~
{"employee_id": "KD-10360", "full_name": "Fatima Sheikh", "account_number": "650378569043", "ifsc": "UTIB0310987", "effective_month": "November 2026"}
~~~~

### GATEWAY prompt (what left)

BLOCKED: BANK_ACCOUNT

### GATEWAY restored answer

~~~~
(none)
~~~~

### MAX prompt (what left)

~~~~
Extract the bank-change request as JSON with keys employee_id, full_name, account_number, ifsc, effective_month.

---
From: [EMAIL_1BPKMW]
Subject: Bank account change
Hello [PERSON_8CSYB5], please update my salary account from the next cycle (November [VALUE_1]). New bank details: account number [BANK_ACCOUNT_1BGCV5], [VALUE_2] [IFSC_2AZ2SP]. My employee code is [EMPLOYEE_ID_65ERE1]. Regards, [PERSON_2M6NEZ]

Respond with JSON only.
~~~~

### MAX restored answer

~~~~
{
  "employee_id": "KD-10360",
  "full_name": "Fatima Sheikh",
  "account_number": "650378569043",
  "ifsc": "UTIB0310987",
  "effective_month": "November 2026"
}
~~~~

### Scores

| condition | correct | score | entity pres. | fact. equiv. | sections | crit masked | unrestored | fabricated | forbidden | blocked |
|---|---|---|---|---|---|---|---|---|---|---|
| raw | True | 1.0 | 1.0 | n/a | n/a | 0 | 0 | 0 | 0 | False |
| gateway | False | 0.0 | 0.0 | n/a | n/a | 0 | 0 | 0 | 0 | True |
| max | True | 1.0 | 1.0 | n/a | n/a | 1 | 0 | 0 | 0 | False |

## extraction-02  (extraction, purpose hr_case_summary, format text)

Difference score: 2.000

### Instruction

~~~~
Give me all PANs and bank account numbers as CSV with columns employee_id,pan,bank_account for the audit sample.
~~~~

### RAW answer

~~~~
employee_id,pan,bank_account
KD-10101,SWNPM0657C,740867537970
KD-10212,AEKPI3659U,5178049164668
KD-10434,XDJPG7590T,94724072425315
KD-10471,ZWMPS2352X,87354688129
KD-11100,YMSPK8994W,2196965496823
~~~~

### GATEWAY prompt (what left)

BLOCKED: AADHAAR, BANK_ACCOUNT, PAN

### GATEWAY restored answer

~~~~
(none)
~~~~

### MAX prompt (what left)

~~~~
Give me all [VALUE_1] and bank account numbers as [VALUE_2] with columns employee_id,pan,bank_account for the audit sample.

---
employee_id,full_name,department,pan,aadhaar,bank_account,ifsc
[EMPLOYEE_ID_58KKYQ],[PERSON_4CJNSW],[VALUE_3],[PAN_202CES],[AADHAAR_0SEKN7],[BANK_ACCOUNT_9GESE0],[IFSC_3XT9KM]
[EMPLOYEE_ID_3GPJZP],[PERSON_7ET2CT],IT,[PAN_2E5VEN],[AADHAAR_832PA8],[BANK_ACCOUNT_1E7D77],[IFSC_50H0FW]
[EMPLOYEE_ID_20MBP4],[PERSON_0D6KX5],[VALUE_4],[PAN_9APD5B],[AADHAAR_4W6Y0E],[BANK_ACCOUNT_81QJC6],[IFSC_6FC89M]
[EMPLOYEE_ID_8G66C0],[PERSON_57D3FR],HR,[PAN_9V37ZN],[AADHAAR_367D63],[BANK_ACCOUNT_5DA63D],[IFSC_580ZJ8]
[EMPLOYEE_ID_9JCE73],[PERSON_10EC4P],[VALUE_5],[PAN_3X6J8T],[AADHAAR_0JYGES],[BANK_ACCOUNT_7C9C6G],[IFSC_6JGEMY]
~~~~

### MAX restored answer

~~~~
employee_id,pan,bank_account
KD-10101,SWNPM0657C,740867537970
KD-10212,AEKPI3659U,5178049164668
KD-10434,XDJPG7590T,94724072425315
KD-10471,ZWMPS2352X,87354688129
KD-11100,YMSPK8994W,2196965496823

Note: this extract contains sensitive personal data (PAN and bank account numbers). Please share it only with authorised audit personnel and through approved secure channels.
~~~~

### Scores

| condition | correct | score | entity pres. | fact. equiv. | sections | crit masked | unrestored | fabricated | forbidden | blocked |
|---|---|---|---|---|---|---|---|---|---|---|
| raw | True | 1.0 | 1.0 | n/a | 1.0 | 0 | 0 | 0 | 0 | False |
| gateway | False | 0.0 | 0.0 | n/a | 0.0 | 0 | 0 | 0 | 0 | True |
| max | True | 1.0 | 1.0 | n/a | 1.0 | 3 | 0 | 0 | 0 | False |

## extraction-03  (extraction, purpose hr_case_summary, format json)

Difference score: 2.000

### Instruction

~~~~
Extract from this payslip a JSON with keys employee_id, month, gross, total_deductions, net_pay (amounts as integers).
~~~~

### RAW answer

~~~~
{"employee_id": "KD-12247", "month": "August 2026", "gross": 124800, "total_deductions": 19640, "net_pay": 105160}
~~~~

### GATEWAY prompt (what left)

BLOCKED: PAN

### GATEWAY restored answer

~~~~
(none)
~~~~

### MAX prompt (what left)

~~~~
Extract from this payslip a JSON with keys employee_id, month, gross, total_deductions, net_pay (amounts as integers).

---
PAYSLIP - AUGUST [VALUE_1]
Employee: [PERSON_3BMYNW] | ID [EMPLOYEE_ID_73G2T0] | PAN [PAN_952XX7]
Earnings: Basic [COMPENSATION_87RFFR]; [VALUE_2] [COMPENSATION_5REB85]; [VALUE_3] [VALUE_4] [VALUE_5]; [VALUE_6] [COMPENSATION_8AGQDG]
Deductions: Provident [VALUE_7] [VALUE_8]; [VALUE_9] [VALUE_10] [VALUE_11]; [VALUE_12] [VALUE_10] (TDS) [VALUE_13]
Net [VALUE_14]: [COMPENSATION_15VZXN]

Respond with JSON only.
~~~~

### MAX restored answer

~~~~
{
  "employee_id": "KD-12247",
  "month": "August 2026",
  "gross": null,
  "total_deductions": null,
  "net_pay": "105160"
}
~~~~

### Scores

| condition | correct | score | entity pres. | fact. equiv. | sections | crit masked | unrestored | fabricated | forbidden | blocked |
|---|---|---|---|---|---|---|---|---|---|---|
| raw | True | 1.0 | 1.0 | n/a | n/a | 0 | 0 | 0 | 0 | False |
| gateway | False | 0.0 | 0.0 | 0.0 | n/a | 0 | 0 | 0 | 0 | True |
| max | False | 0.6 | 1.0 | 0.5 | n/a | 1 | 0 | 0 | 0 | False |

## extraction-05  (extraction, purpose hr_case_summary, format json)

Difference score: 2.000

### Instruction

~~~~
Parse this CV into JSON with keys name, email, phone, dob (YYYY-MM-DD), pan.
~~~~

### RAW answer

~~~~
{"name": "Arvind Krishnan", "email": "arvind.krishnan91@examplemail.in", "phone": "+91 87681 52878", "dob": "1991-02-12", "pan": "WZHPK9162H"}
~~~~

### GATEWAY prompt (what left)

BLOCKED: PAN

### GATEWAY restored answer

~~~~
(none)
~~~~

### MAX prompt (what left)

~~~~
Parse this [VALUE_1] into JSON with keys name, email, phone, dob (YYYY-MM-DD), pan.

---
CURRICULUM [VALUE_2]
Arvind [VALUE_3]
Email: [EMAIL_2TS22F] | Mobile: [PHONE_5XH257]
Date of [VALUE_4]: [DATE_OF_BIRTH_02KXHD] | PAN: [PAN_292GCY]
Experience: [VALUE_5] years in payroll systems; currently [VALUE_6] [VALUE_7] at [VALUE_8] [VALUE_9].

Respond with JSON only.
~~~~

### MAX restored answer

~~~~
{
  "name": "Arvind Krishnan",
  "email": "arvind.krishnan91@examplemail.in",
  "phone": "+91 87681 52878",
  "dob": "12-Feb-1991",
  "pan": "WZHPK9162H"
}
~~~~

### Scores

| condition | correct | score | entity pres. | fact. equiv. | sections | crit masked | unrestored | fabricated | forbidden | blocked |
|---|---|---|---|---|---|---|---|---|---|---|
| raw | True | 1.0 | 1.0 | n/a | n/a | 0 | 0 | 0 | 0 | False |
| gateway | False | 0.0 | 0.0 | n/a | n/a | 0 | 0 | 0 | 0 | True |
| max | False | 0.8 | 1.0 | n/a | n/a | 1 | 0 | 0 | 0 | False |

## summarization-04  (summarization, purpose hr_case_summary, format text)

Difference score: 2.000

### Instruction

~~~~
Summarise ticket PAY-88231 in three lines for the weekly payroll review. Do not include any bank or tax identifiers.
~~~~

### RAW answer

~~~~
Ticket PAY-88231 (Payroll, Resolved): Imran Ansari (KD-11914) reported an extra Rs 4,500 TDS deduction in his October 2026 projection because his HRA exemption was not applied.
Cause: his rent receipts were uploaded on 28 September, after the 25 September declaration cut-off, so they were not picked up; Payroll Ops re-opened the declaration and recomputed the TDS.
Resolution: the excess Rs 4,500 will be refunded in the November 2026 payroll, and the employee has confirmed.
~~~~

### GATEWAY prompt (what left)

BLOCKED: BANK_ACCOUNT, PAN

### GATEWAY restored answer

~~~~
(none)
~~~~

### MAX prompt (what left)

~~~~
Summarise ticket [VALUE_1] in three lines for the weekly payroll review. Do not include any bank or tax identifiers.

---
Ticket [VALUE_1] | Category: Payroll | Raised by: [PERSON_8GVBWE] ([EMPLOYEE_ID_0KERD2]) | Status: Resolved
[[VALUE_2] Sep [VALUE_3]] [PERSON_8GVBWE]: My October [VALUE_4] projection shows an extra deduction of [COMPENSATION_57RANS]. I had uploaded rent receipts but [VALUE_5] exemption was not applied. PAN [PAN_0TEJFD].
[[VALUE_6] Sep [VALUE_3]] Payroll [VALUE_7]: Rent receipts were uploaded on [VALUE_2] September, after the [VALUE_8] September declaration cut-off, so they were not picked up. We have re-opened the declaration and recomputed [VALUE_4].
[[VALUE_9] Sep [VALUE_3]] Payroll [VALUE_7]: The excess [VALUE_4] of [COMPENSATION_57RANS] will be refunded in the November [VALUE_3] payroll to salary account [BANK_ACCOUNT_2RMAD3] (IFSC [IFSC_606XXQ]). Please confirm.
[[VALUE_9] Sep [VALUE_3]] [PERSON_8GVBWE]: Confirmed, thanks.
~~~~

### MAX restored answer

~~~~
Ticket PAY-88231 (Payroll, Resolved): Imran Ansari (KD-11914) reported an extra deduction of Rs 4,500 in the October TDS projection because the rent exemption was not applied.
Root cause: rent receipts were uploaded on 28 September, after the 25 September declaration cut-off, so they were missed; payroll re-opened the declaration and recomputed TDS.
Resolution: the excess TDS of Rs 4,500 will be refunded in the November 2026 payroll, and the employee has confirmed.
~~~~

### Scores

| condition | correct | score | entity pres. | fact. equiv. | sections | crit masked | unrestored | fabricated | forbidden | blocked |
|---|---|---|---|---|---|---|---|---|---|---|
| raw | True | 1.0 | 1.0 | n/a | n/a | 0 | 0 | 0 | 0 | False |
| gateway | False | 0.0 | 0.0 | 0.0 | n/a | 0 | 0 | 0 | 0 | True |
| max | True | 1.0 | 1.0 | 1.0 | n/a | 5 | 0 | 0 | 0 | False |

## case_explanation-04  (case_explanation, purpose hr_case_summary, format text)

Difference score: 1.667

### Instruction

~~~~
Recruiter is asking why the offer for Siddharth Malhotra is on hold. Explain in plain words.
~~~~

### RAW answer

~~~~
Siddharth Malhotra's offer for the Manager, Finance Operations role is on hold because of a mismatch found in his background verification.

His date of birth is 14 March 1996 on his Aadhaar but 14 March 1995 on his Class X certificate. The year differs by one. Under policy clause 7.3, when identity documents show a date-of-birth mismatch, the offer is held until the candidate submits a corrected document or an affidavit.

What this means in practice: he needs to submit a corrected document or an affidavit clarifying his date of birth. Until the hold is lifted, joining is not permitted. Please ask him to send the corrected document or affidavit so the offer can move forward.
~~~~

### GATEWAY prompt (what left)

BLOCKED: AADHAAR, PAN

### GATEWAY restored answer

~~~~
(none)
~~~~

### MAX prompt (what left)

~~~~
Recruiter is asking why the offer for [PERSON_1Y516B] is on hold. Explain in plain words.

---
BGV discrepancy report (vendor: TrustCheck)
Candidate: [PERSON_1Y516B] | Role: Manager, [VALUE_1] [VALUE_2] | Offer status: ON [VALUE_3]
Aadhaar: [AADHAAR_36GHA6]  [VALUE_4]: [PAN_4PFFBM]
Date of birth per [VALUE_5]: [DATE_OF_BIRTH_5YGY1T]
Date of birth per [VALUE_6] [VALUE_7] certificate: [DATE_OF_BIRTH_4DXBQ5]
Policy clause [VALUE_8]: Where identity documents show a date-of-birth mismatch, the offer is held until the candidate submits a corrected document or an affidavit. Joining is not permitted while the offer is on hold.
~~~~

### MAX restored answer

~~~~
The offer for Siddharth Malhotra (Manager, Finance Operations) is on hold because of a background verification mismatch.

The TrustCheck report found that the date of birth on the candidate's Aadhaar does not match the date of birth on the Class X certificate. Under policy clause 7.3, when identity documents show a date-of-birth mismatch, the offer is held until the candidate submits a corrected document or an affidavit. The candidate also cannot join while the offer is on hold.

Next step: the candidate needs to provide a corrected document or an affidavit. Once that is received and the check is cleared, the hold can be lifted and the offer can proceed.
~~~~

### Scores

| condition | correct | score | entity pres. | fact. equiv. | sections | crit masked | unrestored | fabricated | forbidden | blocked |
|---|---|---|---|---|---|---|---|---|---|---|
| raw | False | 0.6667 | 1.0 | n/a | n/a | 0 | 0 | 0 | 0 | False |
| gateway | False | 0.0 | 0.0 | 0.0 | n/a | 0 | 0 | 0 | 0 | True |
| max | False | 0.6667 | 1.0 | 1.0 | n/a | 3 | 0 | 0 | 0 | False |

## case_explanation-05  (case_explanation, purpose hr_case_summary, format text)

Difference score: 1.600

### Instruction

~~~~
Tushar Jadhav ko Hinglish mein samjhao ki uska claim reject kyun hua, kitne din late tha.
~~~~

### RAW answer

~~~~
Tushar, tumhara claim CLM-5521 (cab, Rs 3,860) isliye reject hua kyunki wo time window ke baad submit hua tha, bill ya receipt mein koi problem nahi thi.

Policy 9.1 ke hisaab se claim bill date se 30 din ke andar submit karna zaroori hai.
- Bill date: 20 July 2026, toh last date thi 19 August 2026.
- Tumne claim 12 September 2026 ko submit kiya, yaani bill ke 54 din baad.
- Matlab claim 24 din late tha.

Original receipt lagana sahi tha, lekin deadline miss hone ki wajah se system ne use reject kar diya. Agar koi valid reason tha (jaise travel ya system issue), toh apne manager ke through HR ko exception request bhej sakte ho. Aage se bill ke 30 din ke andar claim daal dena.
~~~~

### GATEWAY prompt (what left)

BLOCKED: UPI_ID

### GATEWAY restored answer

~~~~
(none)
~~~~

### MAX prompt (what left)

~~~~
[PERSON_1BSBCC] ko [VALUE_1] mein samjhao ki uska claim reject kyun hua, kitne din late tha.

---
Claim [VALUE_2] | Employee: [PERSON_1BSBCC] ([EMPLOYEE_ID_4F05VE]) | Type: Local travel (cab) | Amount [COMPENSATION_0PWJ6Q]
Bill date: [VALUE_3] July [VALUE_4] | Submitted: [VALUE_5] September [VALUE_4] | Payout [VALUE_6]: [UPI_ID_3M8Z8V]
Status: REJECTED - "Submitted beyond window"
Policy [VALUE_7]: Reimbursement claims must be submitted within [VALUE_8] days of the bill date.
Employee message: Bhai mera claim reject kyun hua? Bill toh sahi hai, cab ka original receipt bhi laga diya tha.
~~~~

### MAX restored answer

~~~~
Tushar Jadhav, aapka claim reject isliye hua kyunki wo policy 9.1 ki time limit ke baad submit hua. Policy ke hisaab se reimbursement claim bill date ke 30 din ke andar submit karna zaroori hai.

Aapke cab bill ki date 20 July 2026 thi, aur claim 12 September 2026 ko submit hua. Yani bill date se submission tak 30 din ki limit cross ho gayi. Exact late days nikalne ke liye 20 July se 12 September tak ke din gino aur unme se 30 minus kar do; dates masked hain isliye main number nahi likh sakta.

Bill aur original receipt sahi hain, lekin system sirf submission window dekhta hai, isliye claim (Rs 3,860) reject hua. Agar koi valid reason tha late submission ka, toh apne manager ke saath exception request HR ko bhej sakte ho.
~~~~

### Scores

| condition | correct | score | entity pres. | fact. equiv. | sections | crit masked | unrestored | fabricated | forbidden | blocked |
|---|---|---|---|---|---|---|---|---|---|---|
| raw | False | 0.6 | 0.0 | n/a | n/a | 0 | 0 | 0 | 0 | False |
| gateway | False | 0.0 | 0.0 | 0.0 | n/a | 0 | 0 | 0 | 0 | True |
| max | False | 0.2 | 1.0 | 0.3333333333333333 | n/a | 6 | 0 | 0 | 0 | False |

## case_explanation-02  (case_explanation, purpose hr_case_summary, format text)

Difference score: 1.040

### Instruction

~~~~
Kavita Joshi says her Earned Leave balance should be 24 days but the system shows less. Explain what her balance is and why.
~~~~

### RAW answer

~~~~
Hi Kavita, here is how your Earned Leave (EL) balance works out for 2026 (up to 30 September).

Credits:
- Opening balance carried forward: 6 days
- Monthly accrual of 2 days for January to September (9 months): 18 days
- Total credited: 24 days. This is likely the 24 you were expecting.

Leave taken (debits):
- 10-12 Feb: 3 days
- 4-8 May: 5 days
- 17-18 Aug: 2 days
- Total availed: 10 days

Current EL balance: 24 - 10 = 14 days.

So the system is correct: the 24 days is what you have earned in total, but 10 days have already been used. Also, the 2 days of Casual Leave taken on 20 March are tracked in a separate CL balance and do not affect your EL. If you would like, HR can walk through the ledger with you.
~~~~

### GATEWAY prompt (what left)

~~~~
[PERSON_98SVNZ] says her Earned Leave balance should be 24 days but the system shows less. Explain what her balance is and why.

---
Leave ledger 2026 for [PERSON_98SVNZ] ([EMPLOYEE_ID_554ZWW])
entry_date,leave_type,days,note
2026-01-01,EL,6,Opening balance carried forward
2026-01-31,EL,2,Monthly accrual
2026-02-10,EL,-3,Availed 10-12 Feb
2026-02-28,EL,2,Monthly accrual
2026-03-31,EL,2,Monthly accrual
2026-04-30,EL,2,Monthly accrual
2026-05-04,EL,-5,Availed 4-8 May
2026-05-31,EL,2,Monthly accrual
2026-06-30,EL,2,Monthly accrual
2026-07-31,EL,2,Monthly accrual
2026-08-17,EL,-2,Availed 17-18 Aug
2026-08-31,EL,2,Monthly accrual
2026-09-30,EL,2,Monthly accrual
2026-03-20,CL,-2,Casual leave (separate balance)
~~~~

### GATEWAY restored answer

~~~~
Kavita Joshi (KD-10619) has an Earned Leave (EL) balance of 14 days as of 30 September 2026.

How it is calculated from the ledger:
- Opening balance carried forward: 6 days
- Monthly accruals: 9 months (January to September) x 2 days = 18 days
- Total credited: 6 + 18 = 24 days
- Leave availed: 3 days (10-12 Feb) + 5 days (4-8 May) + 2 days (17-18 Aug) = 10 days
- Balance: 24 - 10 = 14 days

So the 24 days she expected is the total EL credited so far (opening balance plus accruals). It does not deduct the 10 days of EL she has already taken. The 2 days of casual leave in March are tracked in a separate CL balance and do not affect her EL. The system figure of 14 days is correct.
~~~~

### MAX prompt (what left)

~~~~
[PERSON_98SVNZ] says her [VALUE_1] Leave balance should be [VALUE_2] days but the system shows less. Explain what her balance is and why.

---
Leave ledger [VALUE_3] for [PERSON_98SVNZ] ([EMPLOYEE_ID_554ZWW])
entry_date,leave_type,days,note
[VALUE_4] balance carried forward
[VALUE_5] accrual
[VALUE_6],-[VALUE_7] [VALUE_8] Feb
[VALUE_9] accrual
[VALUE_10] accrual
[VALUE_11] accrual
[VALUE_12],-[VALUE_13] [VALUE_14] May
[VALUE_15] accrual
[VALUE_16] accrual
[VALUE_17] accrual
[VALUE_18],-[VALUE_19] [VALUE_20] Aug
[VALUE_21] accrual
[VALUE_22] accrual
[VALUE_23],-[VALUE_24] leave (separate balance)
~~~~

### MAX restored answer

~~~~
Kavita Joshi (KD-10619) asked why her Earned Leave balance is lower than the 24 days she expected.

Her balance is the opening carry-forward plus all accruals, minus all leave taken. The ledger shows:
- Opening balance carried forward: 2026-01-01,EL,6,Opening.
- Accruals credited through the year (the 'accrual' entries).
- Leave taken: 3,Availed days in Feb, 5,Availed days in May and 2,Availed days in Aug, which reduce the balance.
- One further debit (2,Casual) belongs to a different leave type with its own separate balance, so it does not reduce her Earned Leave.

The system balance is therefore carry-forward plus accruals less the three deductions above. The likely gap against her 24-day figure is the leave taken in Feb, May and Aug, and possibly she counted only some accrual periods. The ledger values are masked here, so I cannot state the exact number. Ask her to compare each of those deductions with her own records and confirm which ones she disputes.
~~~~

### Scores

| condition | correct | score | entity pres. | fact. equiv. | sections | crit masked | unrestored | fabricated | forbidden | blocked |
|---|---|---|---|---|---|---|---|---|---|---|
| raw | True | 1.0 | 0.0 | n/a | n/a | 0 | 0 | 0 | 0 | False |
| gateway | True | 1.0 | 1.0 | 1.0 | n/a | 0 | 0 | 0 | 0 | False |
| max | False | 0.0 | 1.0 | 0.0 | n/a | 4 | 0 | 0 | 0 | False |

## comparison-03  (comparison, purpose hr_case_summary, format text)

Difference score: 0.780

### Instruction

~~~~
Compare Sourav Chatterjee's travel reimbursements in Q1 (Jan-Mar 2026) with Q2 (Apr-Jun 2026): totals and the change.
~~~~

### RAW answer

~~~~
Sourav Chatterjee (KD-10841) travel reimbursements:

- Q1 (Jan-Mar 2026): 4,200 + 6,350 + 2,800 = INR 13,350
- Q2 (Apr-Jun 2026): 5,100 + 7,400 + 3,950 = INR 16,450

Change: Q2 is INR 3,100 higher than Q1, an increase of about 23.2%.
~~~~

### GATEWAY prompt (what left)

~~~~
Compare [PERSON_6DTB7Z]'s travel reimbursements in Q1 (Jan-Mar 2026) with Q2 (Apr-Jun 2026): totals and the change.

---
Travel claims paid to [PERSON_6DTB7Z] ([EMPLOYEE_ID_5M7TEG])
claim_date,amount_inr
2026-01-14,4200
2026-02-20,6350
2026-03-30,2800
2026-04-12,5100
2026-05-18,7400
2026-06-25,3950
~~~~

### GATEWAY restored answer

~~~~
Travel reimbursements for Sourav Chatterjee (KD-10841):

- Q1 (Jan-Mar 2026): 4,200 + 6,350 + 2,800 = INR 13,350
- Q2 (Apr-Jun 2026): 5,100 + 7,400 + 3,950 = INR 16,450

Change: Q2 is higher by INR 3,100, an increase of about 23.2% over Q1.
~~~~

### MAX prompt (what left)

~~~~
Compare [PERSON_6DTB7Z]'s travel reimbursements in [VALUE_1] (Jan-Mar [VALUE_2]) with [VALUE_3] (Apr-Jun [VALUE_2]): totals and the change.

---
Travel claims paid to [PERSON_6DTB7Z] ([EMPLOYEE_ID_5M7TEG])
claim_date,amount_inr
[VALUE_4]
[VALUE_5]
[VALUE_6]
[VALUE_7]
[VALUE_8]
[VALUE_9]
~~~~

### MAX restored answer

~~~~
I can't calculate the totals or the change because the claim amounts are confidential placeholders, so the real values aren't visible to me. For Sourav Chatterjee (KD-10841), the claims are:

- Q1 (Jan-Mar 2026): 2026-01-14,4200, 2026-02-20,6350, 2026-03-30,2800
- Q2 (Apr-Jun 2026): 2026-04-12,5100, 2026-05-18,7400, 2026-06-25,3950

Once you provide the actual amounts, I will compute:
- Total for each quarter (sum of its claims)
- Change = Apr-Jun total - Jan-Mar total
- Percent change = change / Jan-Mar total x 100
~~~~

### Scores

| condition | correct | score | entity pres. | fact. equiv. | sections | crit masked | unrestored | fabricated | forbidden | blocked |
|---|---|---|---|---|---|---|---|---|---|---|
| raw | True | 1.0 | 1.0 | n/a | n/a | 0 | 0 | 0 | 0 | False |
| gateway | True | 1.0 | 1.0 | 1.0 | n/a | 0 | 0 | 0 | 0 | False |
| max | False | 0.25 | 1.0 | 0.25 | n/a | 3 | 0 | 0 | 0 | False |

## policy_qa-04  (policy_qa, purpose hr_case_summary, format json)

Difference score: 1.030

### Instruction

~~~~
Is Kamala Krishnamurthy eligible for the parental cover? Answer as JSON with keys eligible (true/false) and age_on_policy_start (integer).
~~~~

### RAW answer

~~~~
{"eligible": false, "age_on_policy_start": 76}
~~~~

### GATEWAY prompt (what left)

~~~~
Is Kamala [PERSON_1S9RK4] eligible for the parental cover? Answer as JSON with keys eligible (true/false) and age_on_policy_start (integer).

---
Policy 11.3: Parents can be covered under the group parental cover if their age is 75 years or less on the policy start date (1 April 2026).
Request: [PERSON_01DT04] ([EMPLOYEE_ID_8GSVKZ]) wants to add her mother Kamala [PERSON_1S9RK4], date of birth [born 1950s].

Respond with JSON only.
~~~~

### GATEWAY restored answer

~~~~
{"eligible": null, "age_on_policy_start": null}
~~~~

### MAX prompt (what left)

~~~~
Is [VALUE_1] [PERSON_1S9RK4] eligible for the parental cover? Answer as JSON with keys eligible (true/false) and age_on_policy_start (integer).

---
Policy [VALUE_2]: Parents can be covered under the group parental cover if their age is [VALUE_3] years or less on the policy start date ([VALUE_4] April [VALUE_5]).
Request: [PERSON_01DT04] ([EMPLOYEE_ID_8GSVKZ]) wants to add her mother [VALUE_1] [PERSON_1S9RK4], date of birth [DATE_OF_BIRTH_03TKGR].

Respond with JSON only.
~~~~

### MAX restored answer

~~~~
{"eligible": null, "age_on_policy_start": null}
~~~~

### Scores

| condition | correct | score | entity pres. | fact. equiv. | sections | crit masked | unrestored | fabricated | forbidden | blocked |
|---|---|---|---|---|---|---|---|---|---|---|
| raw | True | 1.0 | n/a | n/a | n/a | 0 | 0 | 0 | 0 | False |
| gateway | False | 0.0 | n/a | 0.0 | n/a | 0 | 0 | 0 | 0 | False |
| max | False | 0.0 | n/a | 0.0 | n/a | 3 | 0 | 0 | 0 | False |

## report_generation-02  (report_generation, purpose hr_case_summary, format text)

Difference score: 1.030

### Instruction

~~~~
Create an arrears statement for Vikram Singh Rathore: a month-wise table and the total arrears payable. Use headings Employee, Period and Total arrears.
~~~~

### RAW answer

~~~~
# Arrears Statement

## Employee

Vikram Singh Rathore (KD-10249)
Revised monthly gross: 85,800 (previously 78,000), a monthly difference of 7,800
Effective 1 April 2026, processed in October 2026 payroll

## Period

April 2026 to September 2026. Arrears are paid on days actually paid: monthly difference x paid days / days in month.

| Month | Days in month | LOP days | Paid days | Arrears (Rs) |
|---|---|---|---|---|
| April 2026 | 30 | 0 | 30 | 7,800 |
| May 2026 | 31 | 0 | 31 | 7,800 |
| June 2026 | 30 | 2 | 28 | 7,280 |
| July 2026 | 31 | 0 | 31 | 7,800 |
| August 2026 | 31 | 0 | 31 | 7,800 |
| September 2026 | 30 | 0 | 30 | 7,800 |

June calculation: 7,800 x 28 / 30 = 7,280.

## Total arrears

Total arrears payable in October 2026 payroll: Rs 46,280 (before applicable statutory deductions such as tax).
~~~~

### GATEWAY prompt (what left)

~~~~
Create an arrears statement for [PERSON_7114P6]: a month-wise table and the total arrears payable. Use headings Employee, Period and Total arrears.

---
Revision letter: [PERSON_7114P6] ([EMPLOYEE_ID_44561Z]) - revised monthly gross [COMPENSATION_5JY45B] (previously [COMPENSATION_3B1WV5]) effective 1 April 2026, processed in October 2026 payroll.
Arrears are paid on days actually paid (monthly difference x paid days / days in month).
month,days_in_month,lop_days
April 2026,30,0
May 2026,31,0
June 2026,30,2
July 2026,31,0
August 2026,31,0
September 2026,30,0
~~~~

### GATEWAY restored answer

~~~~
Arrears Statement

## Employee
Vikram Singh Rathore (KD-10249)
Revised monthly gross: 85800 (previously 78000), effective 1 April 2026, processed in October 2026 payroll.
Monthly difference (D) = revised gross - previous gross. The amounts are masked, so arrears are shown as multiples of D.

## Period
April 2026 to September 2026. Arrears = D x paid days / days in month.

| Month | Days in month | LOP days | Paid days | Arrears |
|---|---|---|---|---|
| April 2026 | 30 | 0 | 30 | D x 30/30 = 1.0000 D |
| May 2026 | 31 | 0 | 31 | D x 31/31 = 1.0000 D |
| June 2026 | 30 | 2 | 28 | D x 28/30 = 0.9333 D |
| July 2026 | 31 | 0 | 31 | D x 31/31 = 1.0000 D |
| August 2026 | 31 | 0 | 31 | D x 31/31 = 1.0000 D |
| September 2026 | 30 | 0 | 30 | D x 30/30 = 1.0000 D |

## Total arrears
Total arrears payable = 5.9333 x D (5 full months plus 28/30 for June), where D = 85800 minus 78000. Please plug in the actual difference to get the rupee amount.
~~~~

### MAX prompt (what left)

~~~~
Create an arrears statement for [PERSON_7114P6]: a month-wise table and the total arrears payable. Use headings Employee, [VALUE_1] and [VALUE_2] arrears.

---
Revision letter: [PERSON_7114P6] ([EMPLOYEE_ID_44561Z]) - revised monthly gross [COMPENSATION_5JY45B] (previously [COMPENSATION_3B1WV5]) effective [VALUE_3] April [VALUE_4], processed in October [VALUE_4] payroll.
Arrears are paid on days actually paid (monthly difference x paid days / days in month).
month,days_in_month,lop_days
April [VALUE_5]
May [VALUE_6]
June [VALUE_7]
July [VALUE_6]
August [VALUE_6]
September [VALUE_5]
~~~~

### MAX restored answer

~~~~
## Employee
Vikram Singh Rathore (KD-10249)
- Revised monthly gross: 85800
- Previous monthly gross: 78000
- Monthly difference: 85800 minus 78000
- Effective 1 April 2026, processed in October 2026 payroll

## Period
Arrears per month = monthly difference x paid days / days in month, where paid days = days in month minus LOP days.

| Month | Days in month / LOP days | Arrears |
|---|---|---|
| April | 2026,30,0 | monthly difference x (days in month - LOP days) / days in month |
| May | 2026,31,0 | as above |
| June | 2026,30,2 | as above |
| July | 2026,31,0 | as above |
| August | 2026,31,0 | as above |
| September | 2026,30,0 | as above |

## Total arrears
Total arrears payable to Vikram Singh Rathore = the sum of the six monthly amounts above, paid in the October 2026 payroll. The exact amount cannot be calculated here because the compensation and day-count values are confidential placeholders.
~~~~

### Scores

| condition | correct | score | entity pres. | fact. equiv. | sections | crit masked | unrestored | fabricated | forbidden | blocked |
|---|---|---|---|---|---|---|---|---|---|---|
| raw | True | 1.0 | 1.0 | n/a | 1.0 | 0 | 0 | 0 | 0 | False |
| gateway | False | 0.0 | 1.0 | 0.0 | 1.0 | 0 | 0 | 0 | 0 | False |
| max | False | 0.0 | 1.0 | 0.0 | 1.0 | 3 | 0 | 0 | 0 | False |

