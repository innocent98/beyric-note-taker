"""S1: create a Workspace Events subscription on users/{id} for conference.v2.started.

    python3 step1_subscribe.py

Which value must {id} be? Tries each candidate in turn and logs every attempt:
  sub        OIDC subject from userinfo
  people_id  People API resourceName without 'people/' (the id the Meet docs call signedinUser.user)
  me         the literal alias
  email      the address
If one succeeds and a later one answers ALREADY_EXISTS, the two are the same identity.
Extra duplicate subscriptions are deleted at the end so S2 sees one event per call.
"""
import time

import common as c

WE = "https://workspaceevents.googleapis.com/v1/"
T = "https://www.googleapis.com/auth/"


def wait_operation(step, op):
    """subscriptions.create returns an Operation; poll it if it is not done yet."""
    for _ in range(20):
        if op.get("done"):
            return op
        time.sleep(2)
        st, op = c.call(step, "GET", WE + op["name"])
        if st != 200:
            break
    return op


def main():
    topic = c.topic_path()
    st, ui = c.call("step1_subscribe", "GET", "https://openidconnect.googleapis.com/v1/userinfo")
    if st != 200:
        c.show(ui); c.die("userinfo failed")
    st, ppl = c.call("step1_subscribe", "GET", "https://people.googleapis.com/v1/people/me",
                     params={"personFields": "names"})
    people_id = ppl["resourceName"].split("/", 1)[1] if st == 200 else None
    print(f"\nFACT oidc sub == people id: {people_id == ui.get('sub')}   (hd claim present: {'hd' in ui})")

    cands = [("sub", ui["sub"])]
    if people_id and people_id != ui["sub"]:
        cands.append(("people_id", people_id))
    cands += [("me", "me"), ("email", ui["email"])]

    created = []
    for label, value in cands:
        body = {
            "targetResource": f"//cloudidentity.googleapis.com/users/{value}",
            "eventTypes": [c.STARTED],
            "notificationEndpoint": {"pubsubTopic": topic},
            "payloadOptions": {"includeResource": False},
        }
        st, op = c.call("step1_subscribe", "POST", WE + "subscriptions", body=body)
        print(f"  candidate {label:9s} -> HTTP {st}")
        if st == 200:
            op = wait_operation("step1_subscribe", op)
            sub = (op.get("response") or {})
            if sub.get("name"):
                created.append((label, sub["name"]))
                c.show(sub)
            else:
                c.show(op)

    if not created:
        print("\nRESULT S1: FAIL, no candidate created a subscription. See local/log/step1_subscribe.log")
        return
    keep_label, keep_name = created[0]
    for label, name in created[1:]:
        if name != keep_name:
            c.call("step1_subscribe", "DELETE", WE + name)
    c.save_state(subscription=keep_name, subscription_target_label=keep_label, subscription_created_at=c.now_iso())
    st, sub = c.call("step1_subscribe", "GET", WE + keep_name)
    c.show(sub)
    print(f"\nRESULT S1: PASS with candidate '{keep_label}'. subscription={keep_name}")
    print(f"           state={sub.get('state')} expireTime={sub.get('expireTime')}")
    print("Next: python3 watch.py   (leave it running while you host the calls)")


if __name__ == "__main__":
    main()
