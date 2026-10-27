# Regulatory Notes

> **NOT LEGAL ADVICE.** Engineering notes derived from PRD section 12 and Appendix F to inform design. They are not conclusions about any specific call, person, or deployment. Laws, rules, and carrier policies change. Last reviewed: **2026-10-27**. Re-review before implementation and before each public release.

| Topic | Design implication | Source |
|---|---|---|
| **FTC Telemarketing Sales Rule, 16 CFR Part 310.** Certain covered telemarketers must honor entity-specific do-not-call requests; the FTC describes practices that obstruct requests. The rule does not automatically apply to every caller or transaction. | Language must separate suspected violations from established findings. Issue-spotting never outputs `VIOLATION_PROVEN`. | [FTC guidance](https://www.ftc.gov/business-guidance/resources/complying-telemarketing-sales-rule); [TSR](https://www.ftc.gov/legal-library/browse/rules/telemarketing-sales-rule) |
| **47 USC 223.** Repeated ringing or calls with prohibited intent can carry criminal exposure in covered circumstances. Open-source status or a suspected scam target does not automatically negate it. | No redial loops, no bulk dialing, bounded sessions, cooldowns, user-initiated only. | [Statute](https://uscode.house.gov/us/usc/119-118/t47/s223/) |
| **FCC / TCPA and AI voices.** FCC interpretation treats AI-generated voices as artificial/prerecorded voices for TCPA purposes. Applicability to specific outbound contact types needs counsel and provider analysis. | Outbound AI module off by default; do not assume a non-marketing purpose removes obligations. | [FCC](https://docs.fcc.gov/public/attachments/DOC-400393A1.pdf) |
| **Recording / interception.** Federal one-party rules do not displace stricter state laws; calls may implicate multiple jurisdictions. An announcement alone may not make recording or live AI processing lawful. | Separate gates for live processing and persistence. When authorization is unknown, disable and fall back to transparent routing. | [RCFP guide](https://www.rcfp.org/introduction-to-reporters-recording-guide/) |
| **Official complaint routes.** FTC directs unwanted-call reports to DoNotCall.gov and fraud detail to ReportFraud.ftc.gov. FCC gathers unwanted-call complaints for enforcement and policy but generally does not resolve individual complaints. | Manual handoff with correct destination; explain that a receipt is not adjudication. | [FTC FAQs](https://consumer.ftc.gov/articles/national-do-not-call-registry-faqs); [FCC](https://consumercomplaints.fcc.gov/hc/en-us/articles/202752940-How-the-FCC-Handles-Your-Complaint) |
| **License.** AGPL/GPL carry warranty and liability language; enforceability varies. OSI criteria prevent field-of-use restrictions in an OSI-open-source license. | AUP governs hosted services only and is not framed as an AGPL restriction. | [GPLv3](https://www.gnu.org/licenses/gpl-3.0.html); [OSD](https://opensource.org/osd) |

## Not yet researched

State-specific telemarketing, recording, and privacy statutes; STIR/SHAKEN specifics; carrier-specific automated-call policies; agency-approved submission APIs (D-006).
