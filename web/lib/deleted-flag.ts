// Set by the meeting page after a delete and read once by the meetings list, which then says
// "Meeting deleted" (a toast does not survive a page change).
export const DELETED_FLAG = "beyric:meeting-deleted";

// Set by the Series page after "Remove this recurring meeting" and read once by the Recurring cards on the
// meetings list, which then says what happened (LEAD-DESIGNED: the designer drew no such control).
export const SERIES_REMOVED_FLAG = "beyric:series-removed";
export const SERIES_REMOVED_MESSAGE = "Recurring meeting removed. Your past meetings stay.";
