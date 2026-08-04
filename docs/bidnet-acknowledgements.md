# BidNet Direct — solicitations behind a required acknowledgement

Why some bids reported `EXTRACTION_FAILED` with every field blank, and what
they report now. Code in `server/app/scrapers/bidnet/scraper.py`; tests in
`server/tests/test_bidnet_acknowledgement.py`.

## What the portal actually does

Requesting certain solicitations does not serve the bid. It redirects:

```
/private/supplier/interception/open-solicitation/9454726201?target=view
        -> /private/supplier/solicitations/9454726201/req-ack
```

That page asks the vendor to Accept or Decline something first. Two real
examples from the failing runs:

| Bid | Acknowledgement | What Accept means |
| --- | --- | --- |
| 9454726201 | **U.S.-Based Company** — "Please acknowledge that the company submitting this proposal is a U.S.-based company." | An attestation about the company |
| 9336819005 | **Mark Schneider** — an addendum PDF (`LP_2026-04.pdf`) | Records that the document was read; visible to the agency in its Document Request List |

Markup, stable across both:

```html
<form name="solicitationForm" method="POST">
  <div class="acknowledgementName mets-field mets-field-view no-label">…</div>
  <div class="noWidthAcknowledgementMessage mets-field mets-field-view no-label">…</div>
  <button type="submit" id="requiredAcknowledgementConfirmPage">Accept</button>
  <button type="submit" id="requiredAcknowledgementDeclinePage">Decline</button>
</form>
```

## Why it looked like a broken scrape

The page defeats a naive detail scrape twice over:

1. **It contains `.mets-field` elements** — the acknowledgement's own. So
   `_scrape_detail`'s wait for that selector *succeeds*, and the load looks
   perfectly healthy.
2. **Every solicitation label is absent**, so all seven fields come back `""`.

`_classify` then saw a record with nothing in it and filed `EXTRACTION_FAILED`,
and the "no fields — reload once" retry loaded the identical wall again. Hence
one gated bid costing three page loads and still being reported as a failure
whose cause was invisible.

## What happens now

`_acknowledgement_gate()` keys on the `/req-ack` redirect **plus** the Accept
button, so an ordinary detail page that merely mentions the word is never
mistaken for one. Detection runs *before* the retry.

A gated bid is exported like any other row, flagged `ACKNOWLEDGEMENT_REQUIRED`,
carrying:

- its detail URL,
- the acknowledgement's name and message, so it is clear what is being asked,
- the heading the acknowledgement page still shows as the title — a URL and
  seven blanks is not a usable spreadsheet row.

It is **not** dropped: a bid you are eligible for but blocked on is worth
chasing by hand. Documents are behind the same wall, so the document phase is
skipped entirely.

The run summary counts them separately from genuine failures:

```
[SUMMARY] run <id> | Scraped: 42 | Fully extracted: 39 | Failed/Fallback: 0
          | Acknowledgement required: 3 | Final Export Count: 42 | Skipped (closing soon): 0
```

and a run warning lists each blocked bid with its URL.

## Accepting automatically (on by default)

`settings.bidnet_auto_accept_acknowledgements` — **on**, at the account
holder's instruction. Runs click Accept and then read the bid normally. Set
`BIDNET_AUTO_ACCEPT_ACKNOWLEDGEMENTS=false` to switch it off, in which case
gated bids are exported flagged as above for a human to accept on the portal.

Every acceptance is a submission the issuing agency can see, so a run never
does it silently: each one is logged and collected onto the run as
`acknowledgements_accepted`, with a run warning naming what was accepted.

Three things the click has to get right:

1. **It must be a real click on the button.** The Accept button is a jQuery
   `commandButton` of `type="submit"` inside
   `<form name="solicitationForm" method="POST">`, which carries a `_csrf`
   hidden input. Posting the form by hand, or navigating anywhere, drops the
   token and the acknowledgement is never recorded. Native Selenium click
   first, JS click as the fallback.
2. **The cookie banner has to go first.** On a fresh session it renders over
   the dialog's button bar and swallows the click ("element click
   intercepted"), which looks exactly like a failed acceptance.
3. **Success is confirmed, not assumed.** The code waits for the Accept button
   to go stale — i.e. the page actually moved on. A swallowed click leaves the
   bid flagged rather than reported as read.

A solicitation can stack several acknowledgements (a pass/fail requirement
*and* a company attestation), so accepting loops up to `MAX_ACK_ACCEPTS` (5),
re-reading the page each time. After the last one the bid is read **from the
page already loaded** — Accept navigates straight to it, so re-requesting the
URL would be a wasted round trip.

### Verified live

Against the three gated bids from the failing run:

| Bid | Acknowledgement | Result |
| --- | --- | --- |
| 9454726201 | U.S.-Based Company | `OK` — 7 fields, 1 document |
| 9336819005 | Mark Schneider | `OK` — 7 fields, 13 documents |
| 9465603653 | Pass/Fail Requirements | `OK` — 7 fields, 6 documents |

3/3 read, 20 documents (18.9 MB), nothing left gated.
