import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EVIDENCE_LABELS } from "@/lib/api/types";
import { EvidenceBadge } from "./Badge";

describe("EvidenceBadge", () => {
  it.each(EVIDENCE_LABELS)("shows %s as visible text", (label) => {
    render(<EvidenceBadge label={label} />);
    expect(screen.getByText(label, { exact: false })).toBeInTheDocument();
  });

  it("explains each label to assistive tech without relying on color", () => {
    render(<EvidenceBadge label="CLAIMED" />);
    expect(screen.getByText(/Stated by the other party; unverified/)).toHaveClass("sr-only");
  });

  it("does not invent a label for unknown input", () => {
    render(<EvidenceBadge label={null} />);
    expect(screen.getByText("Unlabeled")).toBeInTheDocument();
  });
});
