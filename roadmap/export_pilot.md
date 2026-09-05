# Excom — what it is, how the Export desk will run it, and how to switch it on

*Written 5 September 2026, against the live configuration of `erpnextkgopl.local` and what is
deployed at `erp.gargglobalinnovative.com`. Every number below was read off the site, not estimated.*

## What Excom actually is

Excom is the layer that makes every conversation a customer has with GGIL land in one place, attached
to the record that conversation is about. A WhatsApp message, an email, a web-chat enquiry from the
site and a lead form from a marketplace all arrive through different plumbing and, without Excom,
live in different places — a phone in somebody's pocket, a shared mailbox, a spreadsheet. Excom
normalises all of them into two ideas. The first is an **Omni Identity**, which is one human being
however they choose to reach you: a phone number, an email address, an Instagram handle and a
walk-in card can all resolve to the same identity, and merging them later does not lose history. The
second is a **thread**, which is one running conversation with that identity on one channel. Around
those two ideas sit the things a salesperson actually needs: the lead or the opportunity the
conversation is about, the notes people have written on it, who owns it, and which desk it belongs
to.

The important design decision, and the one that separates Excom from a shared inbox, is that Excom
does not keep its own copy of the customer. The lead is a native ERPNext **Lead**; the deal is a
native **Opportunity**; the customer is a native **Customer**. Excom adds a small number of fields to
those records and otherwise stays out of the way, which means the quotation your export agent raises
against a lead is the same lead the WhatsApp conversation is attached to, and ERPNext reporting keeps
working exactly as it did. There is no second CRM to reconcile.

Sitting on top of that is a permission model, and it is worth understanding before anything is
switched on, because it is the part that decides whether the Export desk is a real desk or just a
label. Excom separates two questions that most systems confuse. The first is **what a person may
operate** — answer a message, create a team, hold the WhatsApp access tokens — and that is decided
by the user's role: `Excom User`, `Excom Manager`, or `Excom Admin`, each one including the one
below it. The second is **what a person may see**, and that is decided entirely by the team tree: a
member of a desk sees that desk's conversations and leads, and somebody marked Manager of a team
sees that team and every team beneath it. Being allowed to add a person to a desk is not the same as
being allowed to read every conversation in the company, and Excom now keeps those two things apart.

## Where you are today

The site has four people with Excom roles. Rohit holds all three Excom roles plus every sales role.
Akash, Ankur and Vijay each hold `Excom User` together with ERPNext's `Sales Manager` and
`Sales User`. That combination — **Excom User plus Sales Manager** — is the one this pilot is
written for, because it is what your sales people already have.

Three teams exist. `General` and `Mevabite Accounts` have no members at all, and `Agra Sales` has
one, Akash. So the team tree, which is the thing that decides visibility, is effectively empty: it
has never been used. Lead classification is in the same state. Of 2,081 leads on this bench, 2,080
have no customer type at all; on production there are 1,386 leads, of which 390 have no Omni Identity
either, a gap left by the CRM migration that is worth closing before the desk starts relying on
conversation history.

Restricted lead visibility is currently **off**, which matters more than it sounds. With it off,
what your export agents actually see leads through is the `Sales Manager` role, and `Sales Manager`
carries read, write and **delete** on every lead in the company with no scope of any kind. That is
not a criticism of the role; it is ERPNext's design, and it is why the Export desk cannot mean
anything until the switch is turned on. Turning it on is what makes `Sales Manager` become scoped,
because that role is deliberately not on the bypass list.

## How the Export desk will work day to day

An enquiry arrives. It might be a WhatsApp message to the export number, an email, a form on the
website, or an entry typed in by an agent after a call or an exhibition. Excom resolves the sender to
an identity, opens a thread, and either finds the lead that already belongs to that person or creates
one. If nobody has been given the lead, it is visible to a Sales Master Manager and to nobody else —
that is the rule you asked for, and it holds regardless of how the enquiry arrived. It reaches the
Export desk in one of three ways: a Sales Master Manager hands it over, the export head places it on
the desk, or an assignment rule rotates it there automatically.

From that point the desk works the conversation, and the single most important behaviour to
understand is that **answering claims it**. The moment an export agent replies, the conversation and
that contact's open lead both become theirs and both move onto the Export desk. This is deliberate:
whoever talks to the customer owns the customer, and the alternative — a conversation owned by one
desk and a lead owned by another — is the failure mode that makes people stop trusting the system.
For the same reason, transferring a conversation to another desk takes the lead with it, and writes
a note on both saying why.

A contact and everything hanging off them therefore sit on exactly one desk at a time. If an export
agent ever finds themselves looking at a lead whose conversation they cannot open, or a conversation
whose lead they cannot open, that is a bug and should be reported rather than worked around.

Within the desk the day is ordinary. Agents answer, write notes, tag conversations, and qualify: they
set the customer type — `Export Importer` for this desk — and convert the lead into an Opportunity
when it is real, at which point the opportunity keeps the same owner and the same desk and the
conversation stays attached. Closing a conversation is not final; the next message from that customer
reopens it, which is the correct behaviour for export enquiries that go quiet for a month and then
come back.

The export head, as Manager of the Export desk, sees everything the desk holds without needing any
extra role. If you later create desks beneath Export — a desk per region, say — the head sees those
too, automatically, because sight follows the tree downward. Nobody needs to be given `Excom Manager`
to see their own team's work; that role is for people who need to *create teams and move people
between them*, which on a single-desk pilot is one person at most.

## What the Excom User plus Sales Manager combination gives you

An `Excom User` can now do the whole job of an agent on their own rights: open a conversation, send a
message, write an internal note, tag, transfer, claim, read the activity trail, work the lead and
convert it. That was not true a week ago — writing a note required a System Manager, and the app was
quietly bypassing its own permission checks to make it work — and it is worth knowing that the role
is now self-sufficient. Excom no longer needs `Sales Manager` to function.

`Sales Manager` stays because your export people do ERPNext work that has nothing to do with Excom:
quotations, sales orders, the pricing screens. What it brings to the lead table is unscoped access,
and once restricted visibility is on, that unscoped access is narrowed by Excom's own rule to the
desks the person belongs to. The one thing to keep in mind is that `Sales Manager` also carries
delete on Lead. Under enforcement an agent can only delete a lead they can already see, which for the
Export desk means their own desk's leads, but if you would rather no agent could delete a lead at
all, that is a one-line change to the role and it affects all twenty-two people who hold it, so it is
your call rather than mine.

## Implementation, in the order it should be done

**Build the tree before anything else, and build it as a tree even though there is one desk.** Create
a root team called `Sales` with no parent, then create `Export Desk` with `Sales` as its parent.
Doing this now costs nothing and means the second desk — Agra, Delhi, domestic, whatever comes next —
slots in without re-parenting live work later. Add the export agents to `Export Desk` as Members, and
add the export head to the same desk with the role Manager. If somebody needs to oversee every desk
once there are several, make them Manager of `Sales` rather than giving them a company-wide role.
Teams are managed in Excom under Admin, or from the Desk if you prefer.

**Point the export channel at the desk.** On production the export WhatsApp number is the channel
account named `GGIL Export WB`; open it and restrict its team access to `Export Desk`, so that the
export inbox belongs to export people and does not appear for anyone else. Do the same for any email
account the desk uses. This is the step people skip, and skipping it means the desk is separated on
paper while every agent still sees every inbox.

**Name the shared inbox.** In Excom Settings there is a *Shared inbox team* field. Conversations that
nobody has claimed are visible to the members of whichever team is named there. For a single-desk
pilot the honest answer is `Export Desk` itself, so that an unclaimed export conversation lands in
front of export people rather than nobody. When you add a second desk you will want a small triage
team there instead. If you leave it empty, unclaimed conversations are visible only to an
`Excom Admin`, which is a safe default and a bad experience.

**Classify the leads that belong to the desk.** Excom routes on customer type, and today essentially
nothing is classified. For the pilot you do not need to classify all two thousand: you need the
export leads. Set `customer_type` to `Export Importer` on the leads the desk will work, and set their
team to `Export Desk`. If those leads already have an owner who is an export agent, the backfill
command below does the team for you.

**Decide whether the desk acknowledges automatically, and with what.** On the export source, set an
approved WhatsApp utility template in *Auto Ack Template* and the enquirer gets an immediate
acknowledgement in the export desk's own inbox, which for an export enquiry arriving at 2am from
another timezone is most of the value of the whole system. Until this week that setting silently did
nothing on a Website source — the code only sent acknowledgements for IndiaMART, TradeIndia and Meta
lead ads, so a template configured on a website form was accepted by the form and never used. That is
fixed: a template that is set is a template that gets sent.

Two things sit next to it. *Acknowledge repeat enquiries too* is off by default, so somebody who
writes in twice in a week is not messaged twice; turn it on for a desk where every enquiry is a
distinct order rather than a follow-up. If you do turn it on, leave the cooldown beside it at
24 hours unless you have a reason — five enquiries in a minute would otherwise be five WhatsApp
templates to the same number, and that is how a business number loses its quality rating with Meta.

**Run for a week with visibility still switched off.** Nothing is hidden, nobody is locked out, and
the desk gets used to claiming, transferring and qualifying while the tree and the classifications
settle. This is the cheap way to find out that somebody was left out of the team before it costs
them a customer.

**Then measure, backfill, and switch on.** The measuring step is not optional, because it tells you
exactly how many leads are about to become invisible to whom:

```bash
bench --site erp.gargglobalinnovative.com execute excom.excom.services.crm_visibility.impact_report
bench --site erp.gargglobalinnovative.com execute excom.excom.services.crm_visibility.backfill_teams
```

The first prints how many leads have an owner, how many have a team, how many have neither, and which
users' reach narrows. The second stamps a desk onto every lead that already has an owner, so nobody
loses work they are already doing; it deliberately leaves ownerless leads alone, because those are the
ones meant to go back to the top and be handed out. Only then tick *Restrict lead visibility* in Excom
Settings. If it goes wrong, untick it — the switch is reversible and the old ERPNext behaviour returns
exactly as it was — and the role grants themselves can be undone with
`bench --site <site> execute excom.setup.crm_permissions.revert`.

**Automatic distribution is the last step, not the first.** Once the desk is real and the tree is
right, an assignment rule can rotate new export enquiries round-robin among the agents:

```bash
bench --site erp.gargglobalinnovative.com execute excom.setup.crm_schema.ensure_assignment_rules \
  --kwargs "{'desks': {'Excom Export Desk': ['agent1@ggil.com', 'agent2@ggil.com']}}"
```

Be careful with the other rule in that set, `Excom Intake — Unclassified`, which rotates *every* lead
that has no customer type — imports and background jobs included. On a site where 2,080 leads are
unclassified, switching that on before classifying would hand the entire history to three people
overnight. Leave it off until classification is done.

## What changed this week that the desk will notice

Three things landed after this document was first written, and all three are visible from the leads
screen. **Auto-acknowledgement now works on website enquiries**, described in step five above.
**Every note on a lead is now readable in Excom**: notes an agent typed by hand used to sit in
ERPNext's own child table where Excom could not see them, so a lead with years of history looked
empty in the conversation view; 89 were moved on the working bench and production has more waiting
for its next migrate. And the **leads queue now filters** by country, team, customer type, source,
owner, channel, territory, status, a date range and a free-text search across name, company, email
and phone, with every dropdown built from the values your leads actually carry.

One honest caveat about those filters: the Team filter and the Customer Type filter will both look
broken at first, and they are not. No lead carries a team until the backfill in step six has run,
and exactly one lead in the whole database is classified. The filters work; the data behind them is
what the first four steps of this pilot are for.

## Before any of this, two things need closing on production

The first is the identity gap: 390 of 1,386 production leads have no Omni Identity, which means an
incoming WhatsApp from those customers will not attach to their lead and the agent will see no
history. The cause is fixed — a single link to a deleted contact used to make an identity permanently
unsaveable — but the backlog needs a backfill run.

The second is that production is still running the build from before this week. Everything described
here — the three roles, the team-scoped visibility, the agent's own permissions, the guard fixes —
lives on `main` and is not yet deployed. Deploying is a pull, a migrate and a restart on that bench,
and it should happen before the desk is built, or the screens will not match this document.
