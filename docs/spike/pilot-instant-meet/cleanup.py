"""Teardown after S6 is written up: deletes the Events subscription, the pull subscription and the topic.

    python3 cleanup.py
Then, in the Cloud console, delete the throwaway project (Settings, Shut down) and
remove the app at myaccount.google.com/permissions on the test account.
"""
import common as c

WE = "https://workspaceevents.googleapis.com/v1/"
PS = "https://pubsub.googleapis.com/v1/"


def main():
    s = c.load_state()
    if s.get("subscription"):
        c.call("cleanup", "DELETE", WE + s["subscription"])
    c.call("cleanup", "DELETE", PS + c.sub_path())
    c.call("cleanup", "DELETE", PS + c.topic_path())


if __name__ == "__main__":
    main()
