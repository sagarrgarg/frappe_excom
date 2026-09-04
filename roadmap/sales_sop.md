# Excom — how the sales desk runs

Written against a real walk-through on a nested demo org (`excom.excom.demo`), not from the design docs.
Every rule below was observed, not assumed.

## The one rule that explains everything

**A lead nobody has been given to is visible to a Sales Master Manager and to nobody else.**
It reaches other people exactly three ways: a Sales Master Manager hands it over, a sales head places
it on their team, or auto-assignment does.

After that, you can see a lead when any one of these is true:

| you can see it when | field |
|---|---|
| you own it | `lead_owner` / `opportunity_owner` |
| it is assigned to you | the assignment (a ToDo) |
| it sits on a team you are in | `excom_team` |
| it sits on a team below one you manage | `excom_team` + the team tree |

Conversations follow the same shape: your own, your team's, or unclaimed ones if you are in the
shared inbox team.

## One contact, one desk

A contact's lead, their opportunity and their conversation always sit on the same team. The system
now keeps them together:

- **Claiming a chat** (answering it) takes the lead too, and puts the conversation on your desk.
- **Transferring a chat** to another team moves that contact's open lead and opportunity with it, and
  writes a note on each saying why.
- **Assigning someone from another desk** moves the desk with the work, and records the move.
- **Assigning someone from your own desk or a desk below it** changes nothing: a sales head's
  placement outranks a later claim by one of their own people.

If you ever see a lead whose chat you cannot open, or a chat whose lead you cannot open, that is a
bug. Report it.

## Setting the desk up (one time, a manager does this)

1. **Build the team tree.** Excom → Admin → Teams. A team's parent is who it reports to. A member
   marked *Manager* sees every team beneath theirs.
2. **Put every agent in exactly one team.** An agent in no team sees an empty inbox. Granting the
   Excom User role now adds them to the shared inbox automatically, but move them to their real desk.
3. **Name the shared inbox.** Excom Settings → *Shared inbox team*. Members of that team see chats
   nobody has claimed. Leave it empty and only managers see them.
4. **Switch on lead visibility.** Excom Settings → *Restrict lead visibility*. Before you do:
   ```bash
   bench --site <site> execute excom.excom.services.crm_visibility.impact_report
   bench --site <site> execute excom.excom.services.crm_visibility.backfill_teams
   ```
   The backfill puts a desk on every lead that already has an owner, so nobody loses the work they
   are already doing. Leads with no owner stay unassigned on purpose: those are the ones to hand out.
5. **Turn on auto-assignment**, if you want new leads shared out automatically:
   ```bash
   bench --site <site> execute excom.setup.crm_schema.ensure_assignment_rules --kwargs "{'desks': {'Excom Intake — Unclassified': ['a@x.com','b@x.com']}}"
   ```
   `Excom Intake — Unclassified` is round-robin over every lead with no customer type — **including
   leads created by imports and background jobs**. Give it the people who should actually be
   receiving raw enquiries, and nobody else.

## The day-to-day

**An enquiry arrives** (website form, WhatsApp, marketplace, walk-in typed in by an agent).
If auto-assignment is on it goes to the next person in the rotation and lands on their desk. If not,
it sits with the Sales Master Manager until somebody hands it out.

**Answering a chat claims it.** The moment you reply, the conversation and that contact's open lead
become yours, and both move onto your desk. This is deliberate: whoever talks to the customer owns
the customer.

**Wrong desk?** Transfer the conversation to the right team. The lead goes with it. Add a note
saying why: it is written onto the lead as well as the chat.

**Qualifying.** Classify the lead (customer type), then convert it to an Opportunity. The
opportunity keeps the desk and the owner. The conversation stays attached.

**Closing a chat is not final.** The next message from that customer reopens it.

## The three user roles

What a person may *operate*. Cumulative: each tier includes the one below it.

| | Excom User | Excom Manager | Excom Admin |
|---|---|---|---|
| Answer, note, tag, transfer, claim | yes | yes | yes |
| Create teams, add and remove members, set a member's team role | no | yes | yes |
| Grant Excom roles, reassign someone's work, read the audit log | no | yes | yes |
| Channels, Meta connection, tokens, templates, embed code, settings | no | no | yes |

**Visibility is a different axis, and it does not come from these roles.** It comes from the team
tree: a member sees their desk, and a member marked *Manager of a team* sees that team and every
team beneath it. Being able to add somebody to a desk is not the same as being able to read every
conversation in the company, and until now one role did both. If someone genuinely needs
company-wide sight, make them manager of the top team, or give them Excom Admin.

## What a sales head can and cannot do

| | Sales Master Manager | Sales head (team Manager) | Agent |
|---|---|---|---|
| Sees unassigned leads | yes | no | no |
| Sees their branch's leads | all | yes | own team only |
| Sees conversations | **no** | their branch | own team + shared inbox |
| Hands leads to any desk | yes | within their branch | no |

**The gap to know about:** a Sales Master Manager sees every lead but no conversations, because that
is a sales role, not an Excom role. Give them Excom Manager as well if they should read the chats
behind the leads they hand out. That is a privacy decision, not a technical one.

## Things that will bite you

- **An agent in no team sees nothing.** Not an error message, just an empty inbox. Check Admin →
  Users for the "No team" flag.
- **The intake rule takes every lead without a customer type.** Including bulk imports. Disable it
  before a big import, or classify on import.
- **Transfer moves the desk, not the owner.** If the person changes too, reassign the lead as well.
- **Deleting a user** leaves their open conversations pointing at nobody. Reassign their work first
  (Admin → Users → Reassign).

## Running the walk yourself

```bash
bench --site <site> execute excom.excom.demo.org.build      # 8 users, 5 nested teams
bench --site <site> execute excom.excom.demo.flow.stage1    # leads arrive and get handed out
bench --site <site> execute excom.excom.demo.flow.stage3    # a chat arrives, an agent claims it
bench --site <site> execute excom.excom.demo.flow.stage4    # transfer, then qualify
bench --site <site> execute excom.excom.demo.flow.stage6    # the shared inbox
bench --site <site> execute excom.excom.demo.org.teardown   # remove all of it
```
