-- Seeds one meeting in each state the owner screens draw, into a SCRATCH database (the compose
-- stack of a review, never the pilot). The worker is not running there, so the rows stay as
-- inserted. Run:  docker compose exec -T db psql -U postgres -d notetaker < web/scripts/seed-owner-states.sql
-- All names are placeholders. Template ids 1 to 4 are the four the first migration seeds.
INSERT INTO app_state (id, session_epoch, worker_heartbeat_at) VALUES (1, 0, now())
  ON CONFLICT (id) DO UPDATE SET worker_heartbeat_at = now();

INSERT INTO meetings (url, platform, native_id, template_id, status, step, failure_reason, transcript, speaker_transcript, notes, speakers_incomplete, completion_reason, join_attempts, audio_deleted_at, created_at) VALUES
('https://meet.google.com/aaa-bbbb-ccc', 'google_meet', 'aaa-bbbb-ccc', 2, 'ready', 'done', NULL,
 'Hello everyone. We start with the beta. I can ship on Friday. Good, I take support from Monday.',
 E'[Ada Example]: Hello everyone, thanks for joining. I can ship the beta on Friday.\n[Ben Sample]: Good. I will take the support rota from Monday.\n[Ada Example]: Then we are agreed. I will write the release note by Thursday and send it round for a read.\n[Cy Placeholder]: I have two questions about pricing, but they can wait for the next call.',
 E'## Summary\nAda Example and Ben Sample agreed to ship the **beta** on Friday, with support covered from Monday.\n\n## Key points\n- The beta covers meetings and templates\n- Support rota starts Monday\n- Pricing questions are parked for the next call\n\n## Decisions\n- Ship the beta on Friday\n\n## Action items\n- Ada Example: write the release note by Thursday\n- Ben Sample: set up the support rota\n\n## Open questions\n- What do we charge after the pilot?\n\n## Please check\nNone noted',
 true, NULL, 1, NULL, now() - interval '1 hour'),
('https://meet.google.com/ddd-eeee-fff', 'google_meet', 'ddd-eeee-fff', 1, 'ready', 'done', NULL, 'Short call.', NULL,
 E'## Summary\nA short call. The bot was removed before the end.\n\n## Key points\n- Not much was said', false, 'evicted', 1, NULL, now() - interval '3 hours'),
('https://us02web.zoom.us/j/12345678901?pwd=PLACEHOLDERPWD', 'zoom', '12345678901', 1, 'processing', 'transcribing', NULL, NULL, NULL, NULL, false, NULL, 1, NULL, now() - interval '5 hours'),
('https://us02web.zoom.us/j/98765432109?pwd=PLACEHOLDERPWD', 'zoom', '98765432109', 3, 'failed', 'audio', 'The audio could not be downloaded.', NULL, NULL, NULL, false, NULL, 1, NULL, now() - interval '1 day'),
('https://meet.google.com/ggg-hhhh-iii', 'google_meet', 'ggg-hhhh-iii', 1, 'failed', 'transcribing', 'No speech was found in this recording.', NULL, NULL, NULL, false, NULL, 1, NULL, now() - interval '2 days'),
('https://meet.google.com/jjj-kkkk-lll', 'google_meet', 'jjj-kkkk-lll', 1, 'failed', 'waiting', 'The bot was not admitted to the meeting.', NULL, NULL, NULL, false, NULL, 1, NULL, now() - interval '3 days');
