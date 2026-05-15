"""
Hard-cap Cloud Function for the policies-addon project.

Triggered by a Pub/Sub message from a Cloud Billing budget. When actual spend
crosses 100% of the budget, this function calls cloudbilling.projects.updateBillingInfo
to detach the billing account from the project. Once detached, all billable APIs
on the project stop accepting requests — the Gmail Add-on will start returning
errors, but no further charges can accrue.

Recovery is manual and deliberate: a human re-attaches billing via the console
once they've understood why the budget was breached.

Pattern: https://cloud.google.com/billing/docs/how-to/notify

Deploy with scripts/02-deploy-budget-cap.sh.
"""

from __future__ import annotations

import base64
import json
import logging
import os

import googleapiclient.discovery

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("budget-cap")

PROJECT_ID = os.environ["TARGET_PROJECT_ID"]


def handle_budget_notification(event: dict, context) -> None:
    """Cloud Function entry point (Pub/Sub trigger).

    Pub/Sub message payload schema:
      https://cloud.google.com/billing/docs/how-to/budgets-programmatic-notifications
    """
    data = json.loads(base64.b64decode(event["data"]).decode("utf-8"))
    log.info("Budget notification: %s", json.dumps(data))

    cost = float(data.get("costAmount", 0))
    budget = float(data.get("budgetAmount", 0))
    currency = data.get("currencyCode", "USD")
    fraction = (cost / budget) if budget else 0

    log.info("Spend %.2f %s of %.2f %s budget (%.0f%%)",
             cost, currency, budget, currency, fraction * 100)

    if fraction < 1.0:
        log.info("Below 100%% — no action taken.")
        return

    billing = googleapiclient.discovery.build("cloudbilling", "v1",
                                              cache_discovery=False)
    project_name = f"projects/{PROJECT_ID}"

    log.warning("BUDGET BREACHED. Detaching billing account from %s.", PROJECT_ID)

    # We do NOT call getBillingInfo first: it requires a separate read permission
    # (billing.resourceAssociations.list) that isn't grantable at the project
    # level. updateBillingInfo with an empty billingAccountName is idempotent
    # — calling it when billing is already detached returns the same "no
    # billing account linked" state, not an error.
    result = billing.projects().updateBillingInfo(
        name=project_name,
        body={"billingAccountName": ""},
    ).execute()

    log.warning("Billing detach result: %s", result)
