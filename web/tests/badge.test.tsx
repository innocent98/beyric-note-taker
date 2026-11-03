// @vitest-environment jsdom
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Badge } from "@/components/ui/badge";

// The bot's state can be a whole sentence ("Waiting to be let in. Admit ... in the meeting"); a
// short word must stay on one line, a sentence must be allowed to wrap.
describe("Badge", () => {
  it("lets a sentence wrap", () => {
    const { container } = render(<Badge tone="warning" dot>{"Waiting to be let in. Admit 'Beyric Note Taker (recording)' in the meeting"}</Badge>);
    expect(container.querySelector(".badge")?.classList.contains("badge-wrap")).toBe(true);
  });

  it("keeps a short word on one line", () => {
    const { container } = render(<Badge tone="success" dot>Recording</Badge>);
    expect(container.querySelector(".badge")?.classList.contains("badge-wrap")).toBe(false);
  });
});
