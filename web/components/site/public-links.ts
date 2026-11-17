// Where the public header and footer lead. The landing page is `/` for a visitor and `/welcome` for
// the signed-in owner (`/` is the owner's Meetings page), so every link into one of its sections is
// built on that address and works from any public page, the landing page included.
export const CONTACT_HREF = "mailto:privacy@beyrictech.com";

export const homeFor = (signedIn: boolean): string => (signedIn ? "/welcome" : "/");

export const sectionHref = (home: string, id: "product" | "teams" | "features" | "security"): string => `${home}#${id}`;
