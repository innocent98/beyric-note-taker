"""Create the Pub/Sub topic, let Meet publish to it, create a PULL subscription. Idempotent.

    python3 step0_setup.py
"""
import common as c

PS = "https://pubsub.googleapis.com/v1/"


def main():
    topic, sub = c.topic_path(), c.sub_path()

    st, r = c.call("step0_setup", "PUT", PS + topic, body={})
    if st not in (200, 409):
        c.show(r); c.die("could not create the topic. Is the Cloud Pub/Sub API enabled and billing attached?")

    st, pol = c.call("step0_setup", "POST", PS + topic + ":getIamPolicy", body={})
    if st != 200:
        c.show(pol); c.die("could not read the topic policy")
    member = f"serviceAccount:{c.MEET_SERVICE_ACCOUNT}"
    bindings = pol.get("bindings", [])
    pub = next((b for b in bindings if b["role"] == "roles/pubsub.publisher"), None)
    if pub is None:
        bindings.append({"role": "roles/pubsub.publisher", "members": [member]})
    elif member not in pub["members"]:
        pub["members"].append(member)
    pol["bindings"] = bindings
    st, r = c.call("step0_setup", "POST", PS + topic + ":setIamPolicy", body={"policy": pol})
    if st != 200:
        c.show(r); c.die("could not grant Pub/Sub Publisher to the Meet service account")

    st, r = c.call("step0_setup", "PUT", PS + sub, body={
        "topic": topic, "ackDeadlineSeconds": 60,
        "messageRetentionDuration": "604800s"})  # 7 days, the maximum: keeps S6 lifecycle messages
    if st not in (200, 409):
        c.show(r); c.die("could not create the pull subscription")

    c.save_state(topic=topic, pubsub_subscription=sub)
    print(f"\nOK  topic={topic}\n    pull subscription={sub}\n    publisher granted to {c.MEET_SERVICE_ACCOUNT}")


if __name__ == "__main__":
    main()
