# Approval and autonomy

The approval engine evaluates an action proposal, resource, amount/percentage, marketplace/account, actor, history and configured policy version. It returns `ALLOW`, `REQUIRE_APPROVAL`, or `DENY`. Approval binds to a hash of the exact proposed action; changing the payload invalidates approval.

The initial implementation lives in `commerce_operations.approvals`. Rules are injected into
`ApprovalPolicy`, so thresholds and future rule types can change without changing lifecycle code.
Requests are deduplicated by resource and a SHA-256 hash of the exact requested action. Approval,
rejection, expiry, audit creation, and workflow resumption use the caller's single database
transaction and lock the approval row. Only `pending` approvals may be decided.

Operator endpoints are available under `/api/v1/approvals`: list pending requests, inspect a
request, and post an approve or reject decision. An approved request linked to a `WorkflowRun`
sets that workflow back to `running`; action-specific resumptions can be registered through
`ApprovalActionRegistry` as later workflows are implemented.

## Initial policy

| Action | Initial mode | Notes |
|---|---|---|
| Receive approved product, create request/draft | Autonomous | Idempotent and reversible |
| Place/submit supplier order | Human approval | Commits funds |
| Make supplier payment | Excluded/manual | Requires a later secure payment workflow |
| Record goods receipt | Human initiated | Physical fact must be confirmed |
| Generate/validate listing draft | Autonomous | Proposal only |
| First marketplace publication | Human approval | Public and commercially material |
| Republish unchanged approved content after transient failure | Autonomous | Same payload hash/idempotency key |
| Small price change within configured band and floor | Configurable; off initially | Enable after evidence |
| Large or below-threshold price change | Human approval or deny | Floor is never overridable by AI |
| Ingest orders and reserve stock | Autonomous | Atomic, deterministic |
| Dispatch confirmation | Human/fulfilment signal | Requires physical fulfilment evidence |
| Routine low-risk support response | Autonomous only for allowlisted intents | Audited, template/safety constrained |
| Complaint, dispute, legal, fraud, sensitive issue | Human escalation | Cannot be downgraded by AI alone |
| Refund below threshold | Configurable; approval initially | Autonomy can be enabled per account |
| Refund above threshold/unusual pattern | Human approval/escalation | Idempotent provider call |
| Inventory adjustment/destructive action | Human approval | Reason required |
| Reorder recommendation | Autonomous | Creating/submitting PO still approved |

## Approval lifecycle

`pending -> approved | rejected | expired | cancelled`

Decisions record actor, role, timestamp, rationale and policy version. Separation of duties can later prevent requesters approving their own high-value action. Expired, rejected, already-executed, or payload-mismatched approvals cannot execute.

Autonomy expands by configuration after observing low error rates, bounded loss exposure, reliable rollback/reconciliation, and clear audit evidence. Limits are scoped per tenant/account and action, never hidden in prompts.
