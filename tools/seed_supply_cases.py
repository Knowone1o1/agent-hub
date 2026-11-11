"""
Seeds the first supply-chain case set (AI-drafted, status: draft). Run: py tools/seed_supply_cases.py
Existing case files are never overwritten.

Company: Northwind Home (fictional) sells kitchen and home appliances in Europe and the US.
DCs: Rotterdam, Duisburg, Coventry, Lyon, Madrid, Valencia, Warsaw, Oslo, Barcelona, Antwerp, Chicago.
All names, numbers and events are invented for testing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cases  # noqa: E402

AUTHOR = "Claude (AI draft)"


def ev(eid, owner, rnd, typ, source, content, corrects=None):
    e = {"id": eid, "owner": owner, "release_round": rnd, "type": typ, "source": source, "content": content}
    if corrects:
        e["corrects"] = corrects
    return e


def rubric(*items):
    return [{"criterion": c, "points": p} for c, p in items]


CASES = [
    # ------------------------------------------------------------------ development cases
    {
        "id": "sc-dev-01-customs-hold", "split": "dev", "difficulty": "easy",
        "title": "Stand mixers stuck at customs",
        "brief": "EU customers' orders for SKU A-200 (stand mixer) are being cut short since Monday 10 Nov. Find the root cause and an action plan.",
        "evidence": [
            ev("E1", "demand", 1, "context", "Demand planning report", "EU orders for A-200 last week were 1,050 units vs a forecast of 1,000 (inside the normal Â±10% band). No promotions or new customers."),
            ev("E2", "inventory", 1, "clue", "WMS stock report, Rotterdam DC", "A-200 on-hand at Rotterdam has been 0 since Mon 10 Nov. The ERP shows 400 units in transit with ETA Fri 7 Nov; ATP treats them as available, so orders are accepted and then cut at picking."),
            ev("E3", "logistics", 1, "clue", "Customs broker status feed", "Container MSKU-77 (400 x A-200) has been held at Rotterdam customs since 6 Nov. Reason code: certificate of origin missing or invalid."),
            ev("E4", "supplier", 2, "clue", "Supplier e-mail, Ningbo Kitchen Co.", "The 400 units shipped on time on 20 Oct. A new EU certificate-of-origin format became mandatory on 1 Nov; we have not issued the new-format certificate for this shipment yet. We can issue it within 2 working days once you confirm the consignee details."),
            ev("E5", "logistics", 2, "context", "Carrier schedule", "The ocean leg arrived on schedule; the vessel discharged at Rotterdam on 5 Nov."),
        ],
        "answer_key": {
            "root_cause": "Container MSKU-77 with the 400 replenishment units is held at Rotterdam customs because the supplier has not issued the certificate of origin in the format required since 1 Nov; planning still counts the units as available, so orders are accepted and then cut.",
            "cause_keywords": [["customs"], ["certificate", "origin", "documentation", "document"]],
            "required_evidence": ["E2", "E3", "E4"], "supporting_evidence": ["E1", "E5"],
            "remediation_rubric": rubric(
                ("Get the supplier to issue the new-format certificate and the broker to file it for release", 3),
                ("Cover EU orders meanwhile: reallocate from another DC or expedite stock", 2),
                ("Stop counting held/overdue in-transit stock as available to promise", 2),
                ("Communicate revised dates to affected customers / prioritise orders", 1),
                ("Prevent recurrence: check new documentation rules with suppliers before shipping", 2)),
            "notes": "Demand is a deliberate rule-out. Full credit needs the customs hold AND the documentation cause.",
        },
        "registry": {"features": ["cross_specialist_dependency"],
                     "mechanism_opportunities": "V1: Logistics -> Inventory 'delay/arrival' path is directly useful. V3: Inventory's 'in transit, available' belief is contradicted by E3.",
                     "expected_timing": "Solvable from round 2 once E4 arrives; E2+E3 already point to customs in round 1.",
                     "favoritism_notes": "Simple case; all protocols should solve it. Mainly a calibration case."},
    },
    {
        "id": "sc-dev-02-quality-hold", "split": "dev", "difficulty": "medium",
        "title": "Kettles never shipped after a silent quality hold",
        "brief": "US retail customers report that SKU K-310 (kettle) has been out of stock at the Chicago DC since 3 Nov, two weeks earlier than planned. Find the root cause and an action plan.",
        "evidence": [
            ev("E1", "demand", 1, "context", "Demand planning report", "US K-310 sell-out over the last 4 weeks was 6% above forecast, inside tolerance. No promotions."),
            ev("E2", "inventory", 1, "clue", "WMS / ERP replenishment view", "Chicago K-310 on-hand 0 since 3 Nov. Replenishment PO 4471 (1,200 units) was due in on 27 Oct and has not been received; no ASN exists for it."),
            ev("E3", "supplier", 1, "context", "Supplier portal", "Hanoi Appliance confirmed PO 4471 on 2 Oct with ship date 6 Oct. The portal still shows status 'confirmed'."),
            ev("E4", "logistics", 2, "clue", "Freight forwarder booking system", "There is no booking for PO 4471. The forwarder never received cargo-ready notice from Hanoi Appliance."),
            ev("E5", "supplier", 2, "clue", "Supplier QA report (obtained on request)", "Supplier QA put lot K310-L9 (all 1,200 units of PO 4471) on hold on 4 Oct after a 3% thermostat failure rate at pre-shipment inspection. Rework is expected to finish 21 Nov. The buyer was not notified."),
            ev("E6", "inventory", 3, "distractor", "Cycle count", "A cycle count at Chicago on 1 Nov matched the system for all kettle SKUs (no phantom stock)."),
        ],
        "answer_key": {
            "root_cause": "The supplier put the whole PO 4471 lot on quality hold (thermostat failures) on 4 Oct and never told the buyer, so the replenishment never shipped while the portal still showed it as confirmed.",
            "cause_keywords": [["quality", "qa", "thermostat", "inspection"], ["hold", "not shipped", "never shipped", "no booking"]],
            "required_evidence": ["E2", "E4", "E5"], "supporting_evidence": ["E1", "E3"],
            "remediation_rubric": rubric(
                ("Get a firm rework date and ship good units early / partial shipment", 3),
                ("Bridge the gap: transfer from another DC, air-freight part of the lot, or substitute SKU", 2),
                ("Allocate scarce stock and tell customers revised dates", 1),
                ("Require suppliers to notify quality holds; track confirmed POs against bookings/ASNs", 2),
                ("Follow up on the thermostat quality root cause / inspection plan", 2)),
            "notes": "E3 ('confirmed') is stale, not false. E6 rules out phantom stock.",
        },
        "registry": {"features": ["unknown_downstream_need", "cross_specialist_dependency"],
                     "mechanism_opportunities": "V2: Inventory may not realise the supplier QA detail matters. V3: E4/E5 contradict the 'confirmed' status in E3.",
                     "expected_timing": "Cause visible from round 2 (E4+E5).",
                     "favoritism_notes": "Slightly favours protocols that let Supplier and Logistics findings meet quickly (V1)."},
    },
    {
        "id": "sc-dev-03-promo-forecast", "split": "dev", "difficulty": "easy",
        "title": "Promotion missing from the forecast",
        "brief": "UK web orders for SKU B-150 (blender) have been backordered since 5 Nov while other SKUs are fine. Find the root cause and an action plan.",
        "evidence": [
            ev("E1", "demand", 1, "clue", "Order history", "UK web orders for B-150 from 3 to 9 Nov: 2,400 units vs a forecast of 800."),
            ev("E2", "demand", 2, "clue", "Marketing calendar", "Marketing ran a 30%-off 'Bonfire Week' web promotion on B-150 from 3 Nov. It was not entered in the demand-planning promotion calendar."),
            ev("E3", "inventory", 1, "clue", "WMS stock report, Coventry DC", "B-150 on-hand at Coventry has been 0 since 5 Nov. Safety stock is 250 units, sized for normal weekly variability."),
            ev("E4", "logistics", 1, "context", "Inbound tracker", "Inbound B-150 flows are on plan; no transport delays."),
            ev("E5", "supplier", 1, "context", "Supplier offer", "The supplier is on time. Extra options: 1,500 units by sea in 3 weeks, or 500 units by air in 6 days at +EUR 4/unit."),
        ],
        "answer_key": {
            "root_cause": "An unplanned marketing promotion (not in the demand-planning calendar) tripled B-150 demand, consuming stock and safety stock and causing backorders.",
            "cause_keywords": [["promotion", "promo", "marketing"], ["forecast", "calendar", "not planned", "unplanned"]],
            "required_evidence": ["E1", "E2", "E3"], "supporting_evidence": ["E4", "E5"],
            "remediation_rubric": rubric(
                ("Expedite supply (air 500 + sea 1,500 or similar) and quantify cost", 2),
                ("Manage demand: pause/limit the promotion or manage backorders and customer communication", 2),
                ("Process fix: promotions must be in the demand calendar / S&OP sign-off", 3),
                ("Rebalance stock from other channels or DCs", 1),
                ("Review safety-stock policy for promoted items", 2)),
            "notes": "Logistics and supplier are rule-outs; supplier evidence also gives the expedite options.",
        },
        "registry": {"features": ["negative_control"],
                     "mechanism_opportunities": "Mostly a single-domain (demand) cause; little to forward.",
                     "expected_timing": "Solvable in round 2.",
                     "favoritism_notes": "Low coordination need; protocol overhead should show up as pure cost."},
    },
    {
        "id": "sc-dev-04-phantom-stock", "split": "dev", "difficulty": "medium",
        "title": "Phantom stock after an auto-receipt",
        "brief": "Store replenishment orders for SKU P-420 (pan set) from the Madrid DC are shipping short, although the system shows 900 units in stock. Find the root cause and an action plan.",
        "evidence": [
            ev("E1", "inventory", 1, "clue", "WMS stock report, Madrid DC", "WMS shows 900 P-420 on hand in Madrid, all in bin 12-B."),
            ev("E2", "inventory", 2, "clue", "Picking exception log", "Pickers raised 'empty bin' exceptions for P-420 on 14 of 20 picks this week. Bin 12-B is physically empty."),
            ev("E3", "logistics", 1, "clue", "Receiving log", "The last P-420 receipt was ASN 7781 on 22 Oct: 900 units booked into Madrid by ASN auto-receipt (no physical count at the dock)."),
            ev("E4", "logistics", 2, "clue", "Carrier proof of delivery", "Proof of delivery for ASN 7781 shows 0 pallets delivered to Madrid: the truck was rerouted to the Valencia DC on 22 Oct because of dock congestion."),
            ev("E5", "supplier", 1, "context", "Supplier invoice", "The supplier invoiced 900 units of P-420 shipped on 20 Oct."),
            ev("E6", "demand", 1, "context", "Store demand report", "Store demand for P-420 is in line with forecast."),
            ev("E7", "inventory", 3, "clue", "Message from Valencia DC", "Valencia DC reports 900 unidentified P-420 units received on 23 Oct, sitting in quarantine because no ASN matched them."),
        ],
        "answer_key": {
            "root_cause": "ASN auto-receipt booked 900 P-420 units into Madrid that were physically delivered to Valencia (rerouted truck), creating phantom stock in Madrid; the real units sit in quarantine in Valencia.",
            "cause_keywords": [["auto-receipt", "asn", "booked", "receipt"], ["valencia", "rerouted", "phantom", "not delivered"]],
            "required_evidence": ["E2", "E3", "E4"], "supporting_evidence": ["E1", "E7", "E5"],
            "remediation_rubric": rubric(
                ("Transfer the quarantined units from Valencia to Madrid", 3),
                ("Correct Madrid's inventory record (cycle count / adjustment)", 2),
                ("Stop auto-receipt without physical confirmation, or reconcile ASNs with proof of delivery", 3),
                ("Handle short-shipped store orders (backorders, communication)", 1),
                ("Make carriers notify reroutes to the receiving DC", 1)),
            "notes": "E7 arrives late and confirms where the stock went.",
        },
        "registry": {"features": ["cross_specialist_dependency"],
                     "mechanism_opportunities": "V1: Logistics -> Inventory findings are central. V3: E4 contradicts the receipt in E3.",
                     "expected_timing": "Cause clear from round 2; E7 in round 3 completes the action plan.",
                     "favoritism_notes": "E7 rewards teams that keep going after a plausible answer."},
    },

    # ------------------------------------------------------------------ held-out cases
    {
        "id": "sc-ho-01-rolled-cargo", "split": "heldout", "difficulty": "hard",
        "title": "Air fryers rolled to a later vessel",
        "brief": "German B2B customers are missing deliveries of SKU F-900 (air fryer) this week. Find the root cause and an action plan.",
        "evidence": [
            ev("E1", "logistics", 1, "distractor", "Industry news feed", "A dock strike at the port of Hamburg has delayed many vessels since 8 Nov."),
            ev("E2", "inventory", 1, "clue", "WMS / ERP, Duisburg DC", "F-900 on-hand at Duisburg is 120 units against 1,400 units of open B2B orders. The next inbound, PO 5520 (2,000 units), is expected 12 Nov."),
            ev("E3", "supplier", 1, "context", "Supplier production update", "Production of PO 5520 was completed on 25 Oct."),
            ev("E4", "logistics", 2, "clue", "Booking system", "PO 5520 is booked on vessel MAERSK ELBA, Ningbo -> Rotterdam, then rail to Duisburg; ETA Duisburg 12 Nov."),
            ev("E5", "supplier", 2, "clue", "Supplier e-mail", "We missed the 27 Oct cut-off for MAERSK ELBA: the export carton labels failed your retailer's labelling compliance check and had to be reprinted. The cargo was rolled to the next sailing."),
            ev("E6", "logistics", 3, "correction", "Forwarder correction notice", "Correction: our booking system was not updated after the roll. PO 5520 is now on MSC ANNA, departing Ningbo 10 Nov, ETA Rotterdam 12 Dec.", "E4"),
            ev("E7", "demand", 1, "context", "Demand report", "German B2B demand for F-900 is in line with forecast."),
        ],
        "answer_key": {
            "root_cause": "PO 5520 missed its vessel cut-off because the supplier's carton labels failed retailer compliance and had to be reprinted; the cargo was rolled to a sailing arriving about a month later, while the booking system still showed the old ETA. The Hamburg strike is unrelated (the cargo routes via Rotterdam).",
            "cause_keywords": [["label", "labelling", "labeling", "compliance"], ["rolled", "missed", "cut-off", "cutoff", "next vessel", "later vessel"]],
            "required_evidence": ["E2", "E5", "E6"], "supporting_evidence": ["E3", "E7"],
            "remediation_rubric": rubric(
                ("Bridge the ~1 month gap: air-freight part of the volume or reallocate from other EU DCs", 3),
                ("Allocate available stock across B2B customers and communicate revised dates", 2),
                ("Check labelling compliance with the supplier before cut-off", 2),
                ("Fix ETA visibility: forwarder must update bookings after a roll", 2),
                ("Explicitly rules out the Hamburg strike", 1)),
            "notes": "E1 is a tempting distractor; E4 is stale and corrected by E6 in round 3.",
        },
        "registry": {"features": ["late_correction", "cross_specialist_dependency"],
                     "mechanism_opportunities": "V3: E6 corrects E4; an alert can stop the team relying on the 12 Nov ETA. D0 may propagate the stale ETA.",
                     "expected_timing": "Plausible but wrong answers possible in round 2; full answer needs E6 (round 3).",
                     "favoritism_notes": "Could favour V3 (correction handling). Balanced by HO-06 and HO-02, where alerts are useless."},
    },
    {
        "id": "sc-ho-02-carrier-capacity", "split": "heldout", "difficulty": "medium",
        "title": "Parcel carrier cut its collection capacity",
        "brief": "Next-day delivery promises for UK customers on small kitchen appliances have been missed since Tuesday 11 Nov. Find the root cause and an action plan.",
        "evidence": [
            ev("E1", "logistics", 1, "clue", "Carrier performance dashboard", "ParcelFast on-time delivery from the Coventry DC fell from 97% to 71% since Tue 11 Nov."),
            ev("E2", "logistics", 1, "clue", "ParcelFast customer notice (dated 5 Nov)", "From 11 Nov, daily collection capacity at Coventry is reduced from 4,000 to 2,500 parcels because of a driver shortage."),
            ev("E3", "inventory", 1, "clue", "DC outbound report, Coventry", "Coventry packs about 3,800 parcels a day; since Tuesday roughly 1,300 packed parcels a day are left on the dock overnight."),
            ev("E4", "demand", 1, "context", "Demand report", "Small-appliance orders are 4% above forecast: normal seasonal uplift."),
            ev("E5", "supplier", 1, "context", "Supply status", "No supplier issues; all small-appliance SKUs are in stock."),
            ev("E6", "inventory", 2, "clue", "Contract register", "A second carrier contract (SwiftLine) has 1,500 parcels/day of spare collection capacity at Coventry and has been unused since September."),
        ],
        "answer_key": {
            "root_cause": "ParcelFast cut daily collection capacity at Coventry from 4,000 to 2,500 parcels from 11 Nov, so about 1,300 packed parcels a day miss collection and next-day promises.",
            "cause_keywords": [["carrier", "parcelfast"], ["capacity", "collection", "driver"]],
            "required_evidence": ["E1", "E2", "E3"], "supporting_evidence": ["E6", "E4", "E5"],
            "remediation_rubric": rubric(
                ("Move overflow volume to SwiftLine (spare 1,500/day)", 3),
                ("Clear the backlog (extra/weekend collections) and adjust delivery promises or cut-off times", 2),
                ("Communicate with affected customers", 1),
                ("Monitor carrier capacity notices; capacity SLA in the carrier contract", 2),
                ("Review the long-term carrier mix", 2)),
            "notes": "The notice (E2) was dated 5 Nov: it was known a week in advance.",
        },
        "registry": {"features": ["relay_heavy"],
                     "mechanism_opportunities": "Logistics findings must reach Inventory (who holds the SwiftLine contract); in D0 the coordinator relays them, in V1 they can bypass.",
                     "expected_timing": "Cause in round 1; best plan needs E6 (round 2).",
                     "favoritism_notes": "Favours V1 (relay-heavy by design). Balanced by HO-03/HO-08 where bypass risks dropping useful detail."},
    },
    {
        "id": "sc-ho-03-leadtime-variability", "split": "heldout", "difficulty": "hard",
        "title": "Safety stock that ignores lead-time variability",
        "brief": "Safety stock for SKU T-330 (toaster) keeps running out at the Lyon DC even though average demand and average lead time have not changed. Find the root cause and an action plan.",
        "evidence": [
            ev("E1", "inventory", 1, "clue", "Inventory policy sheet", "Lyon T-330 had stock-outs in 4 of the last 8 weeks. Safety stock is 400 units, set in January using a fixed 21-day lead time and demand variability only."),
            ev("E2", "logistics", 1, "clue", "Quarterly transport review (full report)",
               "Lanes reviewed: 14. Carriers: OceanOne, BlueSea, RailFrance, TransAlp. On-time rate 93% overall; damage rate 0.4%; CO2 per container down 6%; three invoice disputes open with TransAlp (EUR 4,200 total); "
               "pallet-height non-compliance at 2 suppliers; reefer usage n/a; demurrage EUR 11k YTD; average port dwell 3.1 days; customs pre-clearance rate 88%; "
               "Ningbo -> Lyon lane: mean door-to-door lead time 21 days (unchanged), but range 14-35 days over the last 6 months, standard deviation 7 days (last year 2 days); "
               "packaging recommendation: switch to 5-ply cartons on two lanes; driver detention charges flat; next review in February."),
            ev("E3", "demand", 1, "context", "Demand report", "T-330 demand at Lyon is stable at about 300 units/week, coefficient of variation 0.15."),
            ev("E4", "supplier", 1, "context", "Supplier performance", "The supplier ships weekly on schedule; supplier-side lead time is stable."),
            ev("E5", "logistics", 2, "clue", "Routing change log", "In May the transshipment port for the Ningbo -> Lyon lane changed from Marseille to Genoa; the Genoa -> Lyon rail leg has frequent multi-day delays."),
        ],
        "answer_key": {
            "root_cause": "Lead-time variability on the Ningbo-Lyon lane rose sharply (std dev 2 -> 7 days) after the transshipment port moved from Marseille to Genoa, but safety stock is still calculated with a fixed 21-day lead time and ignores lead-time variability.",
            "cause_keywords": [["variability", "variance", "standard deviation", "std", "range"], ["lead time", "lead-time"], ["safety stock"]],
            "required_evidence": ["E1", "E2", "E5"], "supporting_evidence": ["E3", "E4"],
            "remediation_rubric": rubric(
                ("Recalculate safety stock including lead-time variability", 3),
                ("Short-term top-up of T-330 at Lyon", 2),
                ("Fix the lane: review Genoa routing / rail reliability or return to Marseille", 2),
                ("Track lead-time variance as a KPI and review policies regularly", 2),
                ("Check other SKUs on the same lane", 1)),
            "notes": "The key number is buried in a long, mostly irrelevant report (E2).",
        },
        "registry": {"features": ["low_use_detail", "unknown_downstream_need"],
                     "mechanism_opportunities": "V2: a short notice for E2 may hide the one relevant number; if nobody pulls it, the cause is missed. D0 forwards the whole report at higher cost.",
                     "expected_timing": "Cause available from round 1 if E2 is read carefully; E5 (round 2) explains it.",
                     "favoritism_notes": "Designed as a risk case for V2 (unknown need). Balanced by HO-08 where V2 saves cost."},
    },
    {
        "id": "sc-ho-04-moq-capacity", "split": "heldout", "difficulty": "hard",
        "title": "A bigger MOQ on one grinder starves its sister SKU",
        "brief": "The Warsaw DC holds 14 weeks of stock for SKU G-110 (coffee grinder) while sister SKU G-120 is stocked out with backorders. Planners want to know why and what to do.",
        "evidence": [
            ev("E1", "inventory", 1, "clue", "WMS stock report, Warsaw DC", "G-110 on hand 4,200 units (about 14 weeks of cover). G-120 on hand 0 with 600 units backordered."),
            ev("E2", "supplier", 1, "clue", "Contract amendment", "From 1 Sep the supplier raised the MOQ for G-110 from 500 to 3,000 units. The G-120 MOQ is unchanged at 500."),
            ev("E3", "demand", 1, "context", "Demand report", "G-110 demand is stable at about 300 units/week; G-120 demand is about 400 units/week, up 10%."),
            ev("E4", "supplier", 2, "clue", "Supplier production plan", "G-110 and G-120 are built on the same production line. G-120 PO 6120 (2,000 units) was delayed 3 weeks because the line first ran the 3,000-unit G-110 batch."),
            ev("E5", "logistics", 1, "context", "Inbound tracker", "All inbound shipments arrived as planned; no transport delays."),
            ev("E6", "inventory", 2, "distractor", "Parameter change log", "The G-120 reorder point was lowered from 800 to 700 units in August."),
        ],
        "answer_key": {
            "root_cause": "The MOQ increase on G-110 forced a 3,000-unit batch on the production line shared with G-120, which pushed G-120's PO 6120 back three weeks: overstock on G-110, stock-out on G-120.",
            "cause_keywords": [["moq", "minimum order"], ["shared", "same line", "capacity", "production line"]],
            "required_evidence": ["E1", "E2", "E4"], "supporting_evidence": ["E3", "E5"],
            "remediation_rubric": rubric(
                ("Expedite G-120 (partial shipment / priority on the line)", 2),
                ("Negotiate the G-110 MOQ or batch sequencing with the supplier", 3),
                ("Adjust ordering: hold G-110 orders, prioritise G-120 allocation", 2),
                ("Consider a second source for G-120", 1),
                ("Make shared-capacity constraints visible in planning", 2)),
            "notes": "E6 is a small, real change but not the cause.",
        },
        "registry": {"features": ["unknown_downstream_need", "cross_specialist_dependency"],
                     "mechanism_opportunities": "Supplier contract detail (E2) matters to Inventory, which can't predict that. V2 may hide it; V1 Supplier -> Inventory path helps.",
                     "expected_timing": "Cause needs E4 (round 2).",
                     "favoritism_notes": "Mild V1 favour; V2 risk."},
    },
    {
        "id": "sc-ho-05-weather-erp-flag", "split": "heldout", "difficulty": "hard",
        "title": "Cold snap plus a blocked purchase order",
        "brief": "Nordic stores ran out of SKU H-500 (space heater) within 4 days of a cold snap. Find what went wrong and an action plan.",
        "evidence": [
            ev("E1", "demand", 1, "clue", "Sales report", "Nordic H-500 sales in week 45 were 5,600 units vs a forecast of 1,200, during a cold snap (-18 C from 3 Nov)."),
            ev("E2", "demand", 2, "context", "Forecast model notes", "No promotion was running. The forecast model has no weather inputs."),
            ev("E3", "inventory", 1, "clue", "WMS stock report, Oslo DC", "H-500 stock at Oslo was 1,900 units on 3 Nov (safety stock 300) and ran out on 7 Nov."),
            ev("E4", "supplier", 1, "clue", "Supplier stock confirmation", "The supplier has 4,000 finished H-500 units in its Polish warehouse and could deliver to Oslo by truck in 3 days."),
            ev("E5", "logistics", 2, "clue", "Transport planner note", "No truck was booked for H-500: replenishment needs a PO first, and PO creation is blocked because H-500 has carried an 'end of season review' flag in the ERP since 1 Nov."),
            ev("E6", "logistics", 1, "distractor", "Road report", "Some Swedish roads were closed for one day on 4 Nov because of snow."),
        ],
        "answer_key": {
            "root_cause": "A weather-driven demand spike (not in the forecast) emptied Oslo, and replenishment from the supplier's available stock was blocked because an ERP 'end of season review' flag prevented creating the PO.",
            "cause_keywords": [["weather", "cold"], ["flag", "erp", "blocked", "end of season"]],
            "required_evidence": ["E1", "E3", "E5"], "supporting_evidence": ["E4", "E2"],
            "remediation_rubric": rubric(
                ("Remove the flag, raise the PO and truck the supplier's 4,000 units now", 3),
                ("Allocate incoming stock across Nordic stores", 1),
                ("Add weather/event triggers to forecasting", 2),
                ("Review the seasonal-flag process so it can't block replenishment silently", 2),
                ("Seasonal safety-stock adjustment", 2)),
            "notes": "Three domains are needed (demand, inventory, logistics); supplier evidence shows the fix is available.",
        },
        "registry": {"features": ["cross_specialist_dependency"],
                     "mechanism_opportunities": "Needs synthesis across three specialists; coordinator judgment matters (risk for V1 losing synthesis).",
                     "expected_timing": "Full cause needs E5 (round 2).",
                     "favoritism_notes": "Favours D0 synthesis slightly."},
    },
    {
        "id": "sc-ho-06-delisting-control", "split": "heldout", "difficulty": "easy",
        "title": "No supply problem: a planned delisting",
        "brief": "Sales of SKU M-010 (milk frother) dropped 40% in France this month. Is there a supply problem, and what should we do?",
        "evidence": [
            ev("E1", "demand", 1, "clue", "Account report", "France M-010 demand is down 40% because a major retailer delisted M-010 on 1 Nov. The delisting was planned and communicated to sales in September."),
            ev("E2", "inventory", 1, "clue", "WMS stock report", "Stock levels are normal, with no stock-outs; M-010 is slightly overstocked (9 weeks of cover)."),
            ev("E3", "logistics", 1, "context", "Delivery performance", "No delivery issues for M-010."),
            ev("E4", "supplier", 1, "context", "Supplier status", "Supplier performance is normal; next PO (3,000 units) is due in 2 weeks."),
        ],
        "answer_key": {
            "root_cause": "There is no supply problem: demand fell because a major retailer delisted M-010 as planned on 1 Nov; the risk now is overstock.",
            "cause_keywords": [["delist", "delisted", "delisting"]],
            "required_evidence": ["E1", "E2"], "supporting_evidence": ["E3", "E4"],
            "remediation_rubric": rubric(
                ("Lower the forecast and reduce/defer the open PO", 3),
                ("Redeploy excess stock to other markets or channels", 2),
                ("State clearly there is no supply issue (no expediting)", 2),
                ("Feed known delistings into the forecast in future", 2),
                ("Commercial follow-up with the retailer / replacement listings", 1)),
            "notes": "Negative control: nothing to escalate, little to forward.",
        },
        "registry": {"features": ["negative_control"],
                     "mechanism_opportunities": "None by design; protocol overhead shows up as pure cost, and V3 false alarms would be visible.",
                     "expected_timing": "Solvable in round 1-2.",
                     "favoritism_notes": "Penalises protocols with fixed overhead."},
    },
    {
        "id": "sc-ho-07-uom-correction", "split": "heldout", "difficulty": "medium",
        "title": "Short receipt that was a unit-of-measure error",
        "brief": "The Barcelona DC appears to have received far fewer coffee capsules (SKU C-777) than ordered on PO 8890. Find the root cause and an action plan.",
        "evidence": [
            ev("E1", "inventory", 1, "clue", "Goods receipt", "PO 8890 ordered 10,000 units of C-777; the system recorded 1,000 units received on 6 Nov."),
            ev("E2", "supplier", 1, "clue", "Supplier shipping confirmation", "The supplier confirms the full 10,000 units were shipped."),
            ev("E3", "logistics", 1, "clue", "Carrier delivery record", "The carrier delivered 1,000 cartons on 10 pallets with seals intact; the shipment weight matches 10,000 units at 10 units per carton."),
            ev("E4", "inventory", 2, "clue", "Master-data change log", "On 30 Oct a master-data project changed the C-777 receiving unit from 'each' to 'carton', but the conversion factor was left at 1."),
            ev("E5", "inventory", 3, "correction", "Physical recount", "Correction: a physical recount of the PO 8890 receipt found 1,000 cartons x 10 = 10,000 units present.", "E1"),
            ev("E6", "demand", 1, "context", "Demand report", "C-777 demand is slightly below forecast."),
            ev("E7", "supplier", 2, "distractor", "Supplier claims history", "Last month the same supplier short-shipped a different SKU (C-778) by 8%."),
        ],
        "answer_key": {
            "root_cause": "No physical shortage: a master-data change made 'carton' the receiving unit without a conversion factor, so 1,000 cartons (10,000 units) were recorded as 1,000 units.",
            "cause_keywords": [["unit of measure", "uom", "carton", "conversion"], ["master data", "master-data", "conversion factor"]],
            "required_evidence": ["E1", "E3", "E4"], "supporting_evidence": ["E5", "E2"],
            "remediation_rubric": rubric(
                ("Fix the conversion factor and correct the inventory record", 3),
                ("Check other SKUs touched by the master-data project", 2),
                ("Cancel any duplicate reorders or expedites triggered by the false shortage", 2),
                ("Change control for master-data changes", 2),
                ("Do not file a short-shipment claim with the supplier", 1)),
            "notes": "E7 tempts a supplier-blame answer; E5 confirms the diagnosis late.",
        },
        "registry": {"features": ["late_correction"],
                     "mechanism_opportunities": "V3: E3/E5 contradict E1; an alert can stop a supplier claim. D0 may chase the supplier (E7).",
                     "expected_timing": "Diagnosable in round 2 (E4); confirmed in round 3 (E5).",
                     "favoritism_notes": "Some V3 favour; balanced by HO-06."},
    },
    {
        "id": "sc-ho-08-allocation-rule", "split": "heldout", "difficulty": "medium",
        "title": "A system upgrade changed the allocation rule",
        "brief": "Key account HomeMart receives only 60% of its ordered quantity of SKU R-250 (rice cooker) from the Antwerp DC, while smaller accounts get 100%. Find the root cause and an action plan.",
        "evidence": [
            ev("E1", "inventory", 1, "clue", "Allocation report, Antwerp DC", "R-250 available this week: 6,000 units. HomeMart ordered 5,000 and received 3,000; other accounts ordered 3,000 and received all 3,000."),
            ev("E2", "inventory", 2, "clue", "Order-system release notes", "The order-system upgrade on 3 Nov replaced the 'fair share by order week' allocation rule with 'first come, first served by order timestamp'."),
            ev("E3", "demand", 1, "clue", "Order pattern analysis", "HomeMart places one weekly order every Thursday at 18:00; smaller accounts order daily in the morning."),
            ev("E4", "logistics", 1, "context", "Monthly transport KPI pack (full)",
               "On-time delivery 98.1%; damages 0.2%; average truck fill 87%; 412 shipments; 9 carriers; CO2 per pallet down 3%; detention hours 61; claims 4 (EUR 2,300); "
               "lane Antwerp-Rotterdam fill 91%; lane Antwerp-Lille fill 84%; lane Antwerp-Cologne fill 79%; fuel surcharge +1.2%; two near-miss incidents at the yard; new dock scheduling tool live 20 Oct; "
               "no capacity constraints reported; no delays on HomeMart deliveries."),
            ev("E5", "supplier", 1, "context", "Supply status", "Supply is sufficient; the supplier shipped full PO quantities."),
            ev("E6", "demand", 2, "context", "Contract terms", "HomeMart's contract has a 95% fill-rate SLA, with a penalty of 2% of invoice value for each week below target."),
        ],
        "answer_key": {
            "root_cause": "The 3 Nov order-system upgrade switched allocation to first-come-first-served by timestamp, so accounts that order daily in the morning consume stock before HomeMart's weekly Thursday-evening order.",
            "cause_keywords": [["allocation", "first come", "first-come", "fcfs", "timestamp"], ["upgrade", "order system", "rule"]],
            "required_evidence": ["E1", "E2", "E3"], "supporting_evidence": ["E6", "E4", "E5"],
            "remediation_rubric": rubric(
                ("Restore fair-share allocation or reserve stock for key accounts", 3),
                ("Ship HomeMart's shortfall with priority", 2),
                ("Quantify SLA penalty exposure and inform the account team", 2),
                ("Change control/testing for order-system rule changes", 2),
                ("Check other accounts affected by the rule change", 1)),
            "notes": "E4 is long and irrelevant; the cause needs inventory + demand evidence together.",
        },
        "registry": {"features": ["relay_heavy", "low_use_detail", "cross_specialist_dependency"],
                     "mechanism_opportunities": "V2 saves cost on E4 (nobody needs it). Demand -> Inventory evidence must combine (V1 path).",
                     "expected_timing": "Cause needs E2 (round 2) with E3.",
                     "favoritism_notes": "Favours V2 and V1; balanced by HO-03 (V2 risk) and HO-05 (D0 synthesis)."},
    },
]


if __name__ == "__main__":
    keys = lambda d: [r["key"] for r in __import__("lab").ROLE_PACKS[d]["roles"]]
    made = 0
    for c in CASES:
        c = dict(c, domain="supply", author=AUTHOR)
        if (cases.CASES / f"{c['id']}.json").exists():
            print("exists, skipped:", c["id"])
            continue
        cases.save_case(c, keys, editor=AUTHOR)
        made += 1
        print("created:", c["id"])
    print(f"{made} cases created.")
