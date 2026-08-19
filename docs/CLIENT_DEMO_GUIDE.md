# Client demo guide

This is a 5–10 minute walkthrough of synthetic data in **DEMO · SANDBOX** mode. Never describe
the records as live customer, supplier, or marketplace data.

## Before the meeting

```powershell
docker compose up -d postgres
uv run alembic upgrade head
uv run python -m commerce_operations.demo reset
uv run streamlit run streamlit_app.py
```

Confirm the blue Demo badge is visible and open the **Overview** page.

## Demonstration sequence

1. **Opportunity — Overview / Product history**  
   Select **Premium Car Boot Organiser**. Explain: “This product was identified and approved by
   the separate Commerce Intelligence platform. Operations receives a versioned approved
   opportunity without sharing databases.”

2. **Procurement — Procurement**  
   Show the supplier, quantity, landed cost, status, and expected arrival. Explain that the platform
   created the procurement workflow from the approved-product event.

3. **Human control — Pending approvals**  
   Show the synthetic supplier purchase and AI listing approval. Explain: “AI handles routine work;
   a person approves commitments and first publication.”

4. **Inventory — Inventory**  
   Show received stock, cost basis, available quantity, and the low-stock cable clips. Explain that
   receipt and inventory movement are one transactional operation.

5. **AI listing — Listing drafts**  
   Show structured titles, prices, provider/model trace, and approval states. The displayed agent is
   synthetic and performs no paid API call.

6. **Approval — Pending approvals**  
   Revisit the listing approval and explain deterministic validation before human review.

7. **Marketplace — Published listings**  
   Point out the `DEMO-SANDBOX-*` external IDs. Explain that Demo Mode cannot construct live
   marketplace connectors, so no external listing can be created.

8. **Orders — Orders**  
   Show normalised sandbox orders and then return to **Overview** for revenue and realised profit.
   Inventory reflects units sold.

9. **Customer operations — Customer messages**  
   Show the synthetic order-status enquiry, its low-risk classification, and generated response.
   No message was sent externally.

10. **Learning and exception control — Overview / Failures & exceptions**  
    Compare predicted and realised ROI, margin, and selling price for the boot organiser. Then show
    the deliberately failed pantry-bin listing workflow and its retry time.

## Closing story

Return to **Overview**. Use the automation feed to connect events, agents, and workflow status;
use **Action required** to reinforce that automation handles the routine path while people retain
control over commercial decisions and exceptions.

## Safety checklist

- The **DEMO · SANDBOX** badge is visible.
- `COMMERCE_ENVIRONMENT` is not `production`.
- Published IDs begin `DEMO-SANDBOX-`.
- Demo supplier terms state `NO PAYMENT` / `DEMO ONLY`.
- No live marketplace connector, supplier order, payment, or customer-delivery action is invoked.
