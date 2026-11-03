"""Built-in template catalogue + variable helpers. Variables use {{snake_case}}."""
import re

CATEGORIES = ["Career", "Business", "Workplace", "Marketing", "Customer Support"]
ALL_CATEGORIES = CATEGORIES + ["Other"]

VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def extract_variables(*texts: str) -> list:
    seen = []
    for t in texts:
        for v in VAR_RE.findall(t or ""):
            if v not in seen:
                seen.append(v)
    return seen


def substitute(text: str, values: dict) -> str:
    """Replaces {{var}} where a non-empty value is supplied; leaves others intact."""
    def rep(m):
        v = (values or {}).get(m.group(1))
        return str(v).strip() if v is not None and str(v).strip() else m.group(0)
    return VAR_RE.sub(rep, text or "")


def _t(key, category, name, desc, subject, body):
    return {"key": key, "category": category, "name": name,
            "description": desc, "subject": subject, "body": body.strip()}


BUILTINS = [
    # ---- Career
    _t("job-application", "Career", "Job application", "Apply for a role with a short, strong pitch.",
       "Application for {{job_role}} at {{company_name}}",
       """Hi {{recipient_name}},

I'm writing to apply for the {{job_role}} position at {{company_name}}. My background in {{my_skills}} lines up well with what the role needs, and I'd love to contribute to your team.

I've attached my resume and would welcome the chance to discuss how I can help.

Best regards,
{{my_name}}"""),
    _t("interview-followup", "Career", "Interview thank-you", "Thank the interviewer and restate interest.",
       "Thank you – {{job_role}} interview",
       """Hi {{recipient_name}},

Thank you for taking the time to speak with me about the {{job_role}} role at {{company_name}}. I enjoyed learning about the team's goals and I'm even more excited about the opportunity.

Please let me know if I can provide anything else.

Best regards,
{{my_name}}"""),
    _t("referral-request", "Career", "Referral request", "Ask a contact for a referral.",
       "Quick favour – {{job_role}} at {{company_name}}",
       """Hi {{recipient_name}},

I hope you're doing well. I noticed {{company_name}} is hiring a {{job_role}} and I think I'd be a strong fit. Would you be open to referring me or pointing me to the right person?

Happy to share my resume and any context that helps.

Thanks so much,
{{my_name}}"""),
    # ---- Business
    _t("partnership-intro", "Business", "Partnership introduction", "Open a partnership conversation.",
       "Partnership idea for {{company_name}}",
       """Hi {{recipient_name}},

I'm {{my_name}} from {{my_company}}. I've been following {{company_name}} and see a natural fit between our work on {{topic}}.

Would you be open to a 15-minute call next week to explore it?

Best regards,
{{my_name}}"""),
    _t("proposal-followup", "Business", "Proposal follow-up", "Nudge after sending a proposal.",
       "Following up on our proposal for {{company_name}}",
       """Hi {{recipient_name}},

I wanted to follow up on the proposal I sent regarding {{topic}}. Do you have any questions, or is there anything I can clarify to help with your decision?

Best regards,
{{my_name}}"""),
    _t("meeting-request", "Business", "Meeting request", "Ask for a short meeting.",
       "Can we meet about {{topic}}?",
       """Hi {{recipient_name}},

I'd like to schedule a short meeting to discuss {{topic}}. Would {{proposed_time}} work for you? If not, please suggest a time that suits you.

Best regards,
{{my_name}}"""),
    # ---- Workplace
    _t("leave-request", "Workplace", "Leave request", "Request time off from your manager.",
       "Leave request: {{leave_dates}}",
       """Hi {{recipient_name}},

I'd like to request leave on {{leave_dates}}. I'll make sure my work is covered and handed over before I go, and I'm reachable for anything urgent.

Please let me know if this works.

Best regards,
{{my_name}}"""),
    _t("status-update", "Workplace", "Project status update", "Share progress with stakeholders.",
       "Status update: {{project_name}}",
       """Hi {{recipient_name}},

Here's a quick update on {{project_name}}:

- Done: {{completed_items}}
- Next: {{next_steps}}
- Blockers: {{blockers}}

Let me know if you'd like to discuss any of this.

Best regards,
{{my_name}}"""),
    _t("meeting-recap", "Workplace", "Meeting recap", "Summarise decisions and owners.",
       "Recap: {{meeting_name}}",
       """Hi {{recipient_name}},

Thanks for joining {{meeting_name}}. Quick recap of what we agreed:

- Decisions: {{decisions}}
- Action items: {{action_items}}

Please reply if I've missed anything.

Best regards,
{{my_name}}"""),
    # ---- Marketing
    _t("product-launch", "Marketing", "Product launch announcement", "Announce a new product.",
       "Introducing {{product_name}}",
       """Hi {{recipient_name}},

We're excited to announce {{product_name}}, built to help {{target_audience}} with {{key_benefit}}.

Take a look and tell us what you think: {{link}}

Best regards,
{{my_name}}"""),
    _t("cold-outreach", "Marketing", "Cold outreach", "Short, relevant first-touch email.",
       "Idea for {{company_name}}",
       """Hi {{recipient_name}},

I noticed {{company_name}} is working on {{topic}}. At {{my_company}} we help teams like yours with {{key_benefit}}.

Worth a quick chat?

Best regards,
{{my_name}}"""),
    _t("event-invite", "Marketing", "Event invitation", "Invite people to an event.",
       "You're invited: {{event_name}}",
       """Hi {{recipient_name}},

We'd love for you to join us at {{event_name}} on {{event_date}}. It's a great chance to {{event_benefit}}.

Reserve your spot here: {{link}}

Best regards,
{{my_name}}"""),
    # ---- Customer Support
    _t("issue-acknowledgement", "Customer Support", "Issue acknowledgement", "Confirm you received a problem report.",
       "We're on it – {{issue_summary}}",
       """Hi {{recipient_name}},

Thanks for reaching out about {{issue_summary}}, and sorry for the trouble. I'm looking into it now and will update you by {{update_time}}.

Best regards,
{{my_name}}
{{company_name}} Support"""),
    _t("issue-resolved", "Customer Support", "Issue resolved", "Confirm a fix and close the loop.",
       "Resolved: {{issue_summary}}",
       """Hi {{recipient_name}},

Good news – the issue with {{issue_summary}} has been resolved. {{resolution_details}}

If anything still looks off, just reply and I'll help straight away.

Best regards,
{{my_name}}
{{company_name}} Support"""),
    _t("refund-confirmation", "Customer Support", "Refund confirmation", "Confirm a refund has been issued.",
       "Your refund for order {{order_id}}",
       """Hi {{recipient_name}},

I've processed a refund for order {{order_id}}. It should reach your original payment method within {{refund_days}} business days.

Thank you for your patience.

Best regards,
{{my_name}}
{{company_name}} Support"""),
]

BUILTIN_BY_KEY = {t["key"]: t for t in BUILTINS}
