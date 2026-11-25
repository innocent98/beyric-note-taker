import { describe, expect, it } from "vitest";
import { workspaceWords } from "@/lib/workspace-words";

// The one helper that says what a workspace is called (workspaces design, section 6): a class is the same
// workspace in a school's words.
describe("workspaceWords", () => {
  it("a class says Class, Students and Lecturer", () => {
    expect(workspaceWords("class")).toEqual({
      noun: "class",
      label: "Class",
      members: "Students",
      member: "Student",
      owner: "Lecturer",
    });
  });

  it("a team says Team, Members and Owner", () => {
    expect(workspaceWords("team")).toEqual({
      noun: "team",
      label: "Team",
      members: "Members",
      member: "Member",
      owner: "Owner",
    });
  });

  it("anything else, or a response from before classes existed, reads as a team", () => {
    expect(workspaceWords(undefined).label).toBe("Team");
    expect(workspaceWords(null).label).toBe("Team");
    expect(workspaceWords("school").label).toBe("Team");
  });
});
