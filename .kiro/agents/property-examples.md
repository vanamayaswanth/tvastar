# Property Test Templates — Disclosed Reference for qa-engineer

## Isolation property

```python
@given(tenant_a=tenants(), tenant_b=tenants())
def test_tenant_isolation(tenant_a, tenant_b):
    """Data from tenant_a is NEVER visible to queries scoped to tenant_b."""
    assume(tenant_a.id != tenant_b.id)
    leads_a = query_leads(scope=tenant_b)
    assert all(lead.tenant_id != tenant_a.id for lead in leads_a)
```

## Workflow termination property

```python
@given(lead=valid_leads(), actions=lists(workflow_actions()))
def test_workflow_always_terminates(lead, actions):
    """For ANY sequence of actions, the workflow reaches a terminal state."""
    state = WorkflowState(lead)
    for action in actions:
        state = apply_action(state, action)
    assert state.is_terminal or state.has_pending_action
```

## Consent safety property

```python
@given(lead=leads_with_revoked_consent())
def test_no_outbound_when_consent_revoked(lead):
    """A lead with revoked consent NEVER receives outbound calls or messages."""
    actions = get_scheduled_actions(lead)
    assert not any(a.is_outbound for a in actions)
```

## Cooling-off property

```python
@given(phone=phone_numbers(), calls=lists(call_events()))
def test_cooling_off_period(phone, calls):
    """The same phone number is NEVER called more than once within 4 hours."""
    calls_for_phone = [c for c in calls if c.phone == phone]
    for i, j in combinations(calls_for_phone, 2):
        assert abs(i.timestamp - j.timestamp) >= timedelta(hours=4)
```

## Engagement lock property

```python
@given(lead=assigned_leads())
def test_engagement_lock(lead):
    """An assigned lead NEVER receives automated AI outbound actions."""
    assert lead.salesperson_id is not None
    pending_ai_actions = get_ai_outbound_actions(lead)
    assert len(pending_ai_actions) == 0
```

## Stateful testing (RuleBasedStateMachine)

```python
class LeadWorkflowMachine(RuleBasedStateMachine):
    leads = Bundle("leads")

    @rule(target=leads, data=valid_lead_data())
    def create_lead(self, data):
        lead = create_lead(data)
        assert lead.status == "received"
        return lead

    @rule(lead=leads)
    def call_lead(self, lead):
        result = initiate_call(lead)
        assert lead.status in ("calling", "queued")

    @rule(lead=leads)
    def assign_lead(self, lead):
        assume(lead.status == "qualified")
        assign(lead)
        assert lead.salesperson_id is not None
        assert get_ai_outbound_actions(lead) == []

    @invariant()
    def tenant_isolation_holds(self):
        for tenant in self.observed_tenants:
            assert no_cross_tenant_data(tenant)
```
