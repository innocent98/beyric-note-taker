import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getSession } from "@/lib/api/server";
import {
  Alert,
  AppLink,
  Badge,
  Button,
  Card,
  Checkbox,
  Dialog,
  EmptyState,
  Field,
  Icon,
  IconButton,
  ListRow,
  Logo,
  Menu,
  MenuItem,
  NavLink,
  PageHead,
  Segmented,
  SelectField,
  TabButtons,
  Tabs,
  TextareaField,
} from "@/components/ui";

export const metadata: Metadata = {
  title: "Style guide",
  robots: { index: false, follow: false },
};

const swatches = [
  ["sw-bg", "bg", "App background"],
  ["sw-surface", "surface", "Cards, sidebar, inputs"],
  ["sw-text", "text", "Headings, body"],
  ["sw-text-2", "text-2", "Secondary text"],
  ["sw-text-3", "text-3", "Small meta text"],
  ["sw-border", "border", "Borders, dividers"],
  ["sw-border-strong", "border-strong", "Input borders"],
  ["sw-primary", "primary", "Primary actions, logo"],
  ["sw-primary-hover", "primary-hover", "Hover, pressed"],
  ["sw-primary-tint", "primary-tint", "Selected, avatars"],
  ["sw-danger", "danger", "Errors, destructive"],
  ["sw-warning", "warning", "Notices"],
  ["sw-success", "success", "Ready"],
  ["sw-info", "info", "Working"],
  ["sw-teal-light", "teal-light", "Logo on dark"],
  ["sw-night", "night", "Dark landing section"],
  ["sw-ink-2", "ink-2", "Labels, copy inside notes"],
  ["sw-surface-active", "surface-active", "Current nav item"],
] as const;

// Every component and token in the current theme: the parity reference for the designer. In
// production only the logged-in owner sees it (404 for anyone else, as if it did not exist), and
// outside production anyone does. STYLEGUIDE_PREVIEW=1 lets a local production build show it
// without a login, to check the page under the production CSP; compose.yaml never sets it.
export default async function StyleGuidePage() {
  if (process.env.NODE_ENV === "production" && process.env.STYLEGUIDE_PREVIEW !== "1") {
    if (!(await getSession())?.authenticated) notFound();
  }
  return (
    <div className="page">
      <div className="stack">
        <PageHead
          title="Style guide"
          subtitle="Every shared component and design token, as the designer drew them (light only). Narrow the window to check phones."
        />

        <section className="sg-section">
          <h2>Colour tokens</h2>
          <div className="swatches">
            {swatches.map(([cls, name, use]) => (
              <div className="swatch" key={name}>
                <div className={`swatch-chip ${cls}`} />
                <div className="swatch-name">
                  <strong>--{name}</strong>
                  {use}
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="sg-section">
          <h2>Type</h2>
          <div className="card sg-type-sample">
            <h1>Page title, Geist 24px</h1>
            <h2>Card or section title, Geist 15px</h2>
            <p>Body text sits at 14px with generous line height, so notes read like a document, not a dashboard.</p>
            <p className="meta">Meta text, 13px, for dates and hints.</p>
            <p className="mono">Geist Mono for timestamps 00:02:48</p>
          </div>
        </section>

        <section className="sg-section">
          <h2>Logo</h2>
          <div className="card sg-demo">
            <Logo href={null} />
            <Logo size="lg" href={null} />
            <Logo href={null} label={false} />
          </div>
        </section>

        <section className="sg-section">
          <h2>Buttons</h2>
          <div className="card sg-demo">
            <Button type="button">Primary</Button>
            <Button kind="secondary" type="button">Secondary</Button>
            <Button kind="danger" type="button">Danger</Button>
            <Button kind="ghost" type="button">Ghost</Button>
            <Button kind="link" type="button">Link</Button>
            <Button size="sm" type="button">Small</Button>
            <Button size="md" type="button">Medium (36px)</Button>
            <Button size="lg" type="button">Large</Button>
            <Button size="field" type="button">Sign-in size</Button>
            <Button size="xl" type="button">Landing size</Button>
            <Button kind="quiet" type="button">Show</Button>
            <Button disabled type="button">Disabled</Button>
            <Button href="#">As a link</Button>
          </div>
          <div className="card">
            <Button block type="button">Full width</Button>
          </div>
          <div className="sg-night">
            <Logo tone="dark" href={null} />
          </div>
          <div className="sg-teal">
            <Button kind="outline-light" size="xl" type="button">On the teal band</Button>
          </div>
        </section>

        <section className="sg-section">
          <h2>Badges</h2>
          <div className="card sg-demo">
            <Badge>Neutral</Badge>
            <Badge tone="success">Ready</Badge>
            <Badge tone="danger">Failed</Badge>
            <Badge tone="warning">Waiting</Badge>
            <Badge tone="info">Processing</Badge>
            <Badge tone="brand">Brand</Badge>
          </div>
          <div className="card sg-demo">
            <Badge tone="success" dot>Ready</Badge>
            <Badge tone="danger" dot>Failed</Badge>
            <Badge tone="warning" dot>Bot sent</Badge>
            <Badge tone="info" dot>Writing the notes</Badge>
            <Badge tone="success" dot small>Small, in previews</Badge>
          </div>
        </section>

        <section className="sg-section">
          <h2>Alerts</h2>
          <Alert kind="info">Information: something worth knowing.</Alert>
          <Alert kind="notice">Notice: the notes may be incomplete.</Alert>
          <Alert kind="error">Error: something went wrong.</Alert>
        </section>

        <section className="sg-section">
          <h2>Form fields</h2>
          <Card>
            <form className="stack-sm" method="get" action="/styleguide">
              <Field id="sg-text" label="Text field" hint="A hint sits under the field." placeholder="Placeholder" />
              <Field id="sg-icon" label="Field with an icon" icon="link" placeholder="Paste a link" />
              <Field
                id="sg-large"
                label="Sign-in size, with a control inside"
                large
                placeholder="Your password"
                end={<Button kind="quiet" type="button">Show</Button>}
              />
              <Field id="sg-bad" label="Field with an error" defaultValue="oops" error="This value is not valid." />
              <SelectField id="sg-select-sm" label="Compact select" compact options={[["a", "First"], ["b", "Second"]]} />
              <SelectField
                id="sg-select"
                label="Select"
                options={[["a", "First option"], ["b", "Second option"]]}
                defaultValue="b"
              />
              <Field
                id="sg-label-end"
                label="Field with a link in the label row"
                labelEnd={<AppLink href="/styleguide">Forgot password?</AppLink>}
                placeholder="Your password"
              />
              <Checkbox id="sg-check">I agree to the example terms.</Checkbox>
              <Checkbox id="sg-check-bad" error="Tick this box to continue.">
                A box with an error
              </Checkbox>
              <TextareaField id="sg-area" label="Text area" hint="Several lines." />
              <Segmented
                name="sg-seg"
                options={[["one", "One"], ["two", "Two"], ["three", "Three"]]}
                defaultValue="two"
                legend="Example choice"
              />
            </form>
          </Card>
        </section>

        <section className="sg-section">
          <h2>Navigation</h2>
          <div className="card stack-sm">
            <Tabs
              items={[
                { href: "#", label: "Overview", current: true },
                { href: "#", label: "Details" },
                { href: "#", label: "History" },
              ]}
            />
            <TabButtons
              items={[{ id: "sg-notes", label: "Notes" }, { id: "sg-transcript", label: "Transcript" }]}
              defaultId="sg-notes"
              label="Tabs that switch a panel"
            />
            <nav className="nav measure" aria-label="Sidebar example">
              <NavLink href="#" label="Current page" current />
              <NavLink href="#" label="Another page" />
            </nav>
          </div>
        </section>

        <section className="sg-section">
          <h2>Icons</h2>
          <div className="card sg-demo">
            <Icon name="link" /><Icon name="video" /><Icon name="copy" /><Icon name="check" /><Icon name="plus" />
            <Icon name="chevron" /><Icon name="back" /><Icon name="info" /><Icon name="warning" />
            <Icon name="trash" /><Icon name="logout" /><Icon name="arrow" />
            <IconButton icon="copy" label="Copy" />
            <IconButton icon="trash" label="Delete" tone="danger" />
          </div>
        </section>

        <section className="sg-section">
          <h2>Menu and dialog</h2>
          <div className="card sg-demo">
            <Menu label="Download">
              <MenuItem label="PDF" href="#" sub="Best for reading and printing" />
              <MenuItem label="Word document" href="#" sub="Edit in Word or Google Docs" />
            </Menu>
            <Button kind="secondary" type="button" data-dialog-open="sg-dialog">Open a dialog</Button>
          </div>
          <Dialog id="sg-dialog" title="Are you sure?">
            <p>A dialog asks for a decision before something happens.</p>
            <div className="dialog-actions">
              <Button kind="secondary" type="button" data-dialog-close>Cancel</Button>
              <Button type="button" data-dialog-close>Confirm</Button>
            </div>
          </Dialog>
        </section>

        <section className="sg-section">
          <h2>Cards, lists and empty states</h2>
          <Card title="A card with a title">
            <p>Card content goes here.</p>
          </Card>
          <Card flush>
            <ul className="list">
              <ListRow href="#" end={<Badge tone="success">Ready</Badge>}>
                <span className="list-title">A row that links somewhere</span>
                <span className="meta">2 Nov 2026, 15:20 UTC</span>
              </ListRow>
              <ListRow>
                <span className="list-title">A row without a link</span>
              </ListRow>
            </ul>
          </Card>
          <Card flush>
            <EmptyState title="Nothing here yet.">
              An empty state says what will appear and how to start.
            </EmptyState>
          </Card>
        </section>
      </div>
    </div>
  );
}
