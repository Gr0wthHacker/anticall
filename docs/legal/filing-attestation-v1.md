# Filing Attestation - v1

> **DRAFT - requires counsel and jurisdiction-specific adaptation. NOT LEGAL ADVICE.**
> Source: PRD section 21.3. Version: `filing-attestation-v1`. Machine-readable: [`filing-attestation-v1.json`](filing-attestation-v1.json).

> I have reviewed the exact complaint text, recipient, and attachments and believe the statements I am submitting are accurate to the best of my knowledge. I understand that observed call metadata, caller statements, and AI inferences are different types of evidence; that the identity of the responsible caller or business may be unverified; and that filing a complaint does not establish a legal violation or guarantee investigation, compensation, or removal from a calling list. I authorize transmission of the selected information to the named recipient using the described submission channel. I understand I am responsible for confirming that I am entitled to disclose the included material, and that recordings or transcripts may be subject to consent laws.

## Capture requirements

- Approval is captured per submission, never reused across drafts or supplements (PRD 19.3, ENF-014).
- Stored in `submission_approvals`: `draft_id`, `user_id`, `approved_payload_digest` (SHA-256 of the exact payload shown), `attestation_version`, `authorized_at` (UTC).
- The user must be shown the exact complaint text, recipient, channel, and attachment list before the attestation can be accepted.
- If the payload changes after approval, the approval is void and re-attestation is required.
- Do not imply that an FCC complaint commences a private lawsuit or that an FTC report creates an individual remedy (PRD 21.2).
