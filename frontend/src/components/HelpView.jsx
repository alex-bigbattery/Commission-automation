import React from "react";
import { Banner } from "./ui.jsx";
import { SUPPORT_CONTACT } from "../lib/api.js";
import {
  IconInfo, IconSparkle, IconList, IconAudit, IconHistory, IconCloud, IconReports,
  IconSync, IconDownload, IconDollar, IconChart, IconAlert, IconCheck, IconTruck,
  IconFileText, IconRefresh,
} from "./Icons.jsx";

const SECTIONS = [
  ["overview", "Overview"],
  ["quickstart", "Quick Start"],
  ["visuals", "Visual Guides"],
  ["tabs", "The Tabs"],
  ["workflow", "Workflow"],
  ["concepts", "Key Concepts"],
  ["workbook", "The Workbook"],
  ["adjustments", "Making Adjustments"],
  ["trouble", "Troubleshooting"],
  ["faq", "FAQ"],
];

function jump(id) {
  const el = document.getElementById(id);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
}

// Infographics saved under frontend/public/help/ (served at /help/<file>).
const VISUALS = [
  ["commission-review-workflow.png", "Commission Review Workflow",
   "The 8-step monthly flow — Sync → Generate → Review → Approve → Export → Pay — plus the important rules."],
  ["adjustment-decision-tree.png", "Accounting Adjustment Decision Tree",
   "How to resolve a line that appears in Needs Review, step by step."],
  ["adjustments-cheat-sheet.png", "Adjustments Screen Cheat Sheet",
   "What each status, column, and action on the Adjustments screen means."],
  ["quick-reference.png", "Quick Reference (Decision Tree + Source of Truth + Calculation)",
   "One page: how to decide, which reports to trust for payment, and how commission is calculated."],
  ["help-user-guide.png", "Help & User Guide (overview poster)",
   "The full visual guide: tabs, monthly workflow, calculation logic, and common fixes."],
  ["proceso-automatizacion.png", "Proceso de Automatización (Español)",
   "Resumen del proceso: de Zoho a la revisión contable y el reporte final."],
];

function Section({ id, icon: Icon, title, children }) {
  return (
    <section className="card help-section" id={id}>
      <div className="card-header">
        <h3 className="card-title">{Icon && <Icon />} {title}</h3>
      </div>
      <div className="card-body">{children}</div>
    </section>
  );
}

export default function HelpView() {
  const contactHref = SUPPORT_CONTACT
    ? SUPPORT_CONTACT.includes("@") ? `mailto:${SUPPORT_CONTACT}` : SUPPORT_CONTACT
    : null;

  return (
    <div className="page help-page">
      {/* Hero */}
      <section className="card help-hero">
        <div className="card-body">
          <h2 className="help-hero-title"><IconInfo /> Help & User Guide</h2>
          <p className="help-hero-sub">
            Everything you need to run Big Battery's monthly <strong>B2B commissions</strong> — what each screen
            does, the step-by-step flow, the key terms, and how to fix common issues.
          </p>
          <div className="help-toc">
            {SECTIONS.map(([id, label]) => (
              <button key={id} type="button" className="chip" onClick={() => jump(id)}>{label}</button>
            ))}
          </div>
        </div>
      </section>

      {/* Overview */}
      <Section id="overview" icon={IconInfo} title="What this system does">
        <p>
          This app replaces the manual ~2-hour monthly process of building the B2B commission workbook by hand.
          It reads the operational data already synced from <strong>Zoho Books</strong> (sales orders, invoices,
          shipments, items, payments), applies Big Battery's commission rules automatically, lets Accounting
          review and adjust the edge cases, and exports the same B2B workbook your team already uses.
        </p>
        <ul className="help-list">
          <li><strong>Source of truth for payment:</strong> the generated <em>B2B workbook</em> (one sheet per salesperson + B2B Summary).</li>
          <li><strong>Raw Zoho data is never modified</strong> — adjustments live in a separate layer applied on top.</li>
          <li><strong>Everything is auditable:</strong> system value → adjustment → final value is tracked per line.</li>
        </ul>
      </Section>

      {/* Quick start */}
      <Section id="quickstart" icon={IconSparkle} title="Quick start (5 steps)">
        <div className="help-steps">
          {[
            ["Pick the month", "On Generate Commissions, choose the Year and Month you're paying."],
            ["Make sure data is ready", "If it says 'No data for this month', click Sync Zoho and wait (it runs in the background)."],
            ["Generate", "Click Generate Commissions. You'll see KPIs and an Exceptions list."],
            ["Review & adjust", "Go to Adjustments to resolve 'Needs Review' lines (assign salesperson, classify, etc.)."],
            ["Regenerate & download", "Regenerate the workbook and download it. It stays a Draft until pending lines are resolved."],
          ].map(([t, d], i) => (
            <div className="help-step" key={i}>
              <span className="help-step-num">{i + 1}</span>
              <div><strong>{t}</strong><div className="text-faint">{d}</div></div>
            </div>
          ))}
        </div>
      </Section>

      {/* Visual guides */}
      <Section id="visuals" icon={IconChart} title="Visual guides">
        <p className="text-faint" style={{ marginTop: 0 }}>
          Printable infographics of the whole process. Click any image to open it full size.
        </p>
        <div className="help-visuals">
          {VISUALS.map(([file, title, desc]) => (
            <figure className="help-visual" key={file}>
              <a href={`/help/${file}`} target="_blank" rel="noreferrer">
                <img src={`/help/${file}`} alt={title} loading="lazy" />
              </a>
              <figcaption>
                <strong>{title}</strong>
                <span>{desc}</span>
              </figcaption>
            </figure>
          ))}
        </div>
        <p className="text-faint" style={{ fontSize: 12 }}>
          If an image doesn't appear, it hasn't been saved to <code>frontend/public/help/</code> yet.
        </p>
      </Section>

      {/* Tabs */}
      <Section id="tabs" icon={IconList} title="What each tab does">
        <div className="help-glossary">
          <div><span className="help-term"><IconSparkle /> Generate Commissions</span><p>Pick a month and build the B2B workbook in one click. Shows KPIs, exceptions to review, a preview, and a download button.</p></div>
          <div><span className="help-term"><IconList /> Adjustments</span><p>The Accounting review screen. Each commission line shows Calculated → Change → Final. Resolve "Needs Review" lines and approve them.</p></div>
          <div><span className="help-term"><IconAudit /> Audit</span><p>Detailed analysis and validation against the historical workbook. Useful for deep checks.</p></div>
          <div><span className="help-term"><IconHistory /> History</span><p>Browse previous commission workbooks that were completed before.</p></div>
          <div><span className="help-term"><IconCloud /> Zoho Books</span><p>View the operational data exported from Zoho.</p></div>
          <div><span className="help-term"><IconReports /> Reports</span><p>The generated audit reports.</p></div>
        </div>
      </Section>

      {/* Workflow */}
      <Section id="workflow" icon={IconRefresh} title="The monthly workflow, end to end">
        <ol className="help-flow">
          <li><strong><IconSync /> Sync Zoho</strong> — pull the latest sales orders, invoices, shipments and payments into the database. Long syncs run in the background; the screen polls until it's done.</li>
          <li><strong><IconSparkle /> Generate</strong> — the engine classifies every invoice line, routes it by CF.Sales Team, nets out returns, looks up MAP price → discount → commission tier, splits current vs prior period, and rolls everything into the B2B Summary.</li>
          <li><strong><IconList /> Review in Adjustments</strong> — anything the system can't decide automatically is marked <span className="badge badge-yellow">Needs Review</span>: a missing salesperson, a Company/Executive account to classify, etc.</li>
          <li><strong><IconCheck /> Approve</strong> — make the accounting decision (assign / classify / exclude / override), add a required reason, and mark Approved.</li>
          <li><strong><IconDownload /> Regenerate & download</strong> — the final workbook reflects your decisions. While any line is still pending, the workbook is clearly a <span className="badge badge-yellow">DRAFT</span>; once everything is resolved it becomes <span className="badge badge-green">FINAL</span>.</li>
        </ol>
      </Section>

      {/* Key concepts */}
      <Section id="concepts" icon={IconChart} title="Key concepts (glossary)">
        <div className="help-glossary">
          <div><span className="help-term"><IconDollar /> Calculated vs Final Commission</span><p><strong>Calculated</strong> is what the system computed automatically. <strong>Final</strong> is what will actually be paid after your accounting decisions. <strong>Change</strong> is the difference.</p></div>
          <div><span className="help-term"><IconTruck /> Returned quantity</span><p>Commission is paid only on quantity kept (invoiced − returned). A fully returned line earns <strong>$0</strong>; a partial return is prorated. This runs for every salesperson and every order automatically.</p></div>
          <div><span className="help-term">CF.Sales Team routing</span><p>Lines are routed exactly like Accounting does it — only <strong>B2B</strong> and Exec/Company lines belong in this workbook; B2C lines go to the separate B2C file.</p></div>
          <div><span className="help-term">MAP, discount &amp; rate</span><p>The MAP (list) price drives the discount (1 − revenue/MAP). The discount maps to a commission tier. <strong>Non-salaried reps (Brett, Leslie, Carmen, Garrett) earn the higher tier.</strong></p></div>
          <div><span className="help-term"><IconHistory /> Current vs Prior period</span><p>Orders placed in the month count as current; orders from earlier months invoiced now are shown under "prior periods".</p></div>
          <div><span className="help-term"><IconAlert /> Needs Review / Pending</span><p>A line that can't be finalized until you decide something — usually a missing salesperson or an account to classify. Pending lines are held out of totals until resolved.</p></div>
          <div><span className="help-term">Company / Executive Account</span><p>Sales that belong to the house account or an executive, not an individual rep. Classify them in Adjustments.</p></div>
          <div><span className="help-term"><IconCheck /> Draft vs Final</span><p>The workbook is a <strong>Draft</strong> while pending lines or missing shipment data exist, and <strong>Final</strong> only once everything is resolved and approved.</p></div>
          <div><span className="help-term">Reconciliation (Check A / Check B)</span><p>Two internal checks that must read <strong>0</strong>: the salesperson sheets add up to the summary, and the components add up to the Total to Pay.</p></div>
        </div>
      </Section>

      {/* Special rules confirmed by Accounting */}
      <Section id="special-rules" icon={IconAlert} title="Special rules (confirmed by Accounting — June 2026)">
        <div className="help-glossary">
          <div>
            <span className="help-term">Bruce Taylor — Company Account</span>
            <p>Lines where Zoho shows <strong>Bruce Taylor</strong> as salesperson go to <em>Company Account</em>. Bruce has a special arrangement and is <strong>not paid direct commissions</strong> from invoices marked as Company Account. These lines appear as <span className="badge badge-yellow">Needs Review</span> and must be classified manually each month.</p>
          </div>
          <div>
            <span className="help-term">Marshall Neipert — Executive Account</span>
            <p>Lines where Zoho shows <strong>Marshall Neipert</strong> go to <em>Executive Account</em> and require review. Any exception commission for an executive account must be approved by <strong>Marshall</strong> himself.</p>
          </div>
          <div>
            <span className="help-term">Inactive / Non-B2B names</span>
            <p>The following names are no longer active B2B reps and will <strong>never auto-assign</strong>: BB Affiliate Investment, Dmitry Gorobets, Michael Northcutt, Joseph Mohney, Jessica Key, Kara Wagner, Alexis McCarthy, River Michelle Harrington, Ryan Murphy. If they appear, the line is flagged for manual review.</p>
          </div>
          <div>
            <span className="help-term">Ticket# classification</span>
            <p>Only a <strong>real support ticket</strong> (numeric 1–4 digits, e.g. 650 or 1234) is auto-excluded as non-commissionable. A <strong>quote reference</strong> in the Ticket# field (e.g. QUO-04421) is <em>not</em> excluded — commission calculates normally. Any other non-empty Ticket# format is flagged for review but not auto-excluded.</p>
          </div>
          <div>
            <span className="help-term">B2C non-commissionable = primary ticket filter</span>
            <p>The main way tickets stay out of payable is the <strong>CF.Sales Team = "B2C - RC Team (No Commissionable)"</strong> tag — those never enter the B2B workbook. The Ticket# field is a <em>secondary</em> catch, because reps sometimes forget to fill the ticket number.</p>
          </div>
          <div>
            <span className="help-term">Possible ticket / price anomaly</span>
            <p>Because Ticket# is sometimes left blank, the system also flags a line as <strong>Possible ticket</strong> when the invoiced amount is far above the MAP (e.g. a $3k item invoiced at $400k). This is a <em>review flag only</em> — never an automatic exclusion.</p>
          </div>
          <div>
            <span className="help-term">Kits and $0 lines</span>
            <p>SKUs starting with <strong>K</strong> are kits: one bundled price carries the commission. The <strong>$0 component lines</strong> inside a kit (or $0 ticket lines) earn <strong>no separate commission</strong> — they are excluded and shown in the audit for reference.</p>
          </div>
          <div>
            <span className="help-term">Payment confirmation (e.g. SolarGuysPro)</span>
            <p>Lines on unpaid invoices, or payments that can't be tied to a quote/order, are flagged <strong>Confirm payment before payout</strong>. These stay in review until Accounting confirms the payment applies.</p>
          </div>
          <div>
            <span className="help-term">Missing-MAP lines (monitored)</span>
            <p>Lines whose SKU has <strong>no MAP</strong> (often custom/"Miscellaneous" or ticket lines) can't be priced, so they earn <strong>$0 commission</strong> by default. They're <em>monitored</em> as a possible custom/ticket risk; most are already held or excluded by the B2C, Exe./Comp., $0-line, or roster rules. If future missing-MAP <em>payable</em> exposure becomes material, Accounting can enable a dedicated review flag.</p>
          </div>
          <div>
            <span className="help-term">Dylan Nava / Customer Service — B2C coupon rule</span>
            <p>These reps have a coupon-based commission rule. <strong>B2C-RC Team coupon = commissionable.</strong> B2C-Web Marketing coupon = not commissionable. Orders without a coupon = organic sales. These lines do <em>not</em> go into the standard B2B payable — they require coupon verification before being approved.</p>
          </div>
          <div>
            <span className="help-term">Exe./Comp. Account default</span>
            <p>Invoices tagged <em>Exe./Comp. Account</em> in Zoho are <strong>excluded from payable by default</strong> unless Accounting manually approves them. They always appear in the Needs Review queue.</p>
          </div>
          <div>
            <span className="help-term">Roster changes</span>
            <p>New reps should be added to the roster <strong>on their date of hire</strong>. Contact <strong>Bruce Taylor</strong> to add a new salesperson. The AR employee makes classification calls; escalate to Honey for unusual cases.</p>
          </div>
        </div>
      </Section>

      {/* Workbook */}
      <Section id="workbook" icon={IconFileText} title="The generated workbook, sheet by sheet">
        <div className="help-glossary">
          <div><span className="help-term">B2B Summary</span><p>The front page: totals per salesperson, Company/Executive accounts, and the Draft/Final status banner.</p></div>
          <div><span className="help-term">One sheet per salesperson</span><p>The detail lines (current + prior), with Payment Terms, Shipment info, MAP, discount and commission — the live formulas Jennifer expects.</p></div>
          <div><span className="help-term">Adjustments Audit</span><p>Every line with <em>System → Adjustment → Final</em>, plus quantities, returns and Suggested Action — the full audit trail.</p></div>
          <div><span className="help-term">Reconciliation</span><p>Engine-computed values with Check A and Check B (both must be 0), shown live.</p></div>
          <div><span className="help-term">B2B Payable vs Jennifer</span><p>The authoritative comparison — "Our Commission" comes from the same engine as the payable workbook, lined up against Jennifer's numbers.</p></div>
          <div><span className="help-term">R_SO / R_INV / R_SH / R_LP</span><p>Reference data for the month (sales orders, invoices, shipments, the MAP price list used).</p></div>
          <div><span className="help-term">Legacy … (Diagnostic Only)</span><p>The old diagnostic sheets, kept for reference. <strong>Do not use them for payment</strong> — each one says so in its first cell.</p></div>
        </div>
      </Section>

      {/* Adjustments how-to */}
      <Section id="adjustments" icon={IconList} title="Making adjustments (the review actions)">
        <p>Open <strong>Adjustments</strong>, pick the month, and work the <span className="badge badge-yellow">Needs Review</span> queue. Each row has quick actions that open a guided panel:</p>
        <div className="help-actions">
          <div><span className="pill">Assign</span> Credit the line to a specific salesperson.</div>
          <div><span className="pill">Company</span> Move it to the Company Account.</div>
          <div><span className="pill">Exec</span> Move it to the Executive Account.</div>
          <div><span className="pill pill-danger">Exclude</span> Remove it from commission (Final becomes $0).</div>
          <div><span className="pill">Override</span> Fix the commissionable amount, MAP, or discount.</div>
          <div><span className="pill pill-success">Approve</span> Mark the line ready to pay.</div>
        </div>
        <Banner type="info" icon={IconInfo}>
          A <strong>Reason / Notes</strong> is required on every change (for the audit trail), and high-impact actions
          (exclude / override) ask you to confirm. Nothing is auto-assigned or auto-approved — Accounting always
          makes the final call. After saving, click <strong>Regenerate workbook</strong>.
        </Banner>
      </Section>

      {/* Troubleshooting */}
      <Section id="trouble" icon={IconAlert} title="Troubleshooting & common messages">
        <div className="help-glossary">
          <div><span className="help-term">"The server took too long" / timeout</span><p>The server may be waking up or busy syncing. Wait a minute and click <strong>Retry</strong>. The first action after idle can take 1–2 minutes.</p></div>
          <div><span className="help-term">"No data for this month"</span><p>Click <strong>Sync Zoho</strong> and wait for it to finish, then Generate.</p></div>
          <div><span className="help-term">Sync seems to run forever</span><p>It runs in the background and the screen polls it. Big syncs take a few minutes; you can keep working and check back.</p></div>
          <div><span className="help-term">Shipment data missing</span><p>If the banner says shipments aren't synced, the workbook stays Draft. Sync shipments, then regenerate.</p></div>
          <div><span className="help-term">Numbers differ from Jennifer</span><p>Expected — the difference is the manual-judgment items (reassignments, special discounts). Resolve them in Adjustments; the <em>B2B Payable vs Jennifer</em> sheet shows exactly where.</p></div>
        </div>
        <Banner type="warning" icon={IconAlert}>
          Still stuck after retrying?{" "}
          {contactHref ? <a href={contactHref}>Contact the developer</a> : <strong>Contact the developer</strong>} and
          share what you were doing plus any error code shown on screen.
        </Banner>
      </Section>

      {/* FAQ */}
      <Section id="faq" icon={IconInfo} title="FAQ">
        <details className="help-faq"><summary>Does generating change the data in Zoho?</summary><p>No. The app only reads Zoho data. Adjustments are stored separately and applied on top — raw Zoho is never modified.</p></details>
        <details className="help-faq"><summary>Can I re-run a month safely?</summary><p>Yes. Generation is repeatable; your saved adjustments are re-applied each time.</p></details>
        <details className="help-faq"><summary>Why is the workbook marked DRAFT?</summary><p>Because there are still pending/unassigned lines, missing shipment data, or unapproved adjustments. Resolve them and it turns FINAL.</p></details>
        <details className="help-faq"><summary>Where do returns come from?</summary><p>From the Sales Order's returned quantity in Zoho. Commission is netted automatically for every line.</p></details>
        <details className="help-faq"><summary>Which sheet is the source of truth?</summary><p>The per-salesperson sheets + B2B Summary in the generated workbook, and the "B2B Payable vs Jennifer" sheet. Legacy sheets are diagnostic only.</p></details>
      </Section>

      <p className="help-footer text-faint">
        Big Battery — Commission Automation · need more help?{" "}
        {contactHref ? <a href={contactHref}>Contact the developer</a> : "Contact the developer"}.
      </p>
    </div>
  );
}
