// Text kept identical to app/templates/privacy.html: Google compares the policy with the app, and
// tests/legal.test.ts fails if the two drift.
export function PrivacyContent({ days }: { days: number }) {
  return (
    <>
      <h1>Privacy Policy</h1>
      <p className="meta">Effective 2 November 2026 &middot; Last updated 4 November 2026</p>
      <p>This policy explains what information Beyric Note Taker collects, how we use it, who we share it with, and the choices you have. It applies to our website, web app and emails.</p>

      <h2 id="who">Who we are</h2>
      <p>Beyric Note Taker is operated by Beyrichq LTD ("Beyric", "we", "us"), Lagos, Nigeria. We are the data controller for account and usage information. For meeting content that our users record, we act on behalf of the user who sent the note taker, and that user decides what is recorded.</p>
      <p>Questions about this policy or how we handle your information go to <a href="mailto:privacy@beyrictech.com">privacy@beyrictech.com</a>.</p>

      <h2 id="collect">Information we collect</h2>
      <h3>Information you give us</h3>
      <ul>
        <li>Account details: your name and email address, and a password (stored only in hashed form) or, if you sign in with Google, your Google account identifier. We also keep a count of the hours of meetings recorded for your account, and, if you join the waiting list, your email address.</li>
        <li>Settings and content you create: notes templates and vocabulary terms. From each set of notes we also keep the lists of decisions, open questions and Please check items it contains, so you can search and tick them off, and, for a Please check item about a name or term that may have been misheard, the word as it was transcribed. From the action items we keep tasks: a short title, who it is assigned to, a due date, a status and a short quote from the transcript that shows where it came from, and any tasks you add yourself.</li>
        <li>Messages you send us, such as support requests.</li>
        <li>When you share notes by email: the names and email addresses of the people you send them to, and the optional message you write, kept with the meeting so you can see who it went to.</li>
      </ul>
      <h3>Meeting content</h3>
      <p>When you send the note taker to a meeting, we collect:</p>
      <ul>
        <li>Audio from the meeting and, where the platform provides it, the display names of participants and who was speaking.</li>
        <li>The transcript produced from that audio.</li>
        <li>Notes, summaries, decisions and action items produced from the transcript.</li>
        <li>Meeting details such as the platform, meeting link or ID, date, time and duration.</li>
        <li>Files you export (a PDF or Word file of your notes, and of the transcript if you choose it) are built when you ask and are not stored.</li>
      </ul>
      <p>We do not record video or screen sharing.</p>
      <h3>Teams</h3>
      <p>People you invite to your team can see tasks assigned to them, including a short quote from the meeting, and any meeting you choose to share with the team. Meetings are private to you unless you share them. We keep the name and email address you enter for an invitation until it is accepted, cancelled or expires.</p>
      <h3>Information collected automatically</h3>
      <ul>
        <li>Server logs, which include your IP address, the pages you request and the time of each request. We use them to run and secure the service.</li>
        <li>One cookie that keeps you signed in, described under Cookies below.</li>
      </ul>

      <h2 id="google">Google user data</h2>
      <p>If you connect your Google account, we ask only for the access the features below need, and Google shows you each permission before you agree.</p>
      <p>If you sign in with Google, we receive your name, email address and Google account identifier, and nothing else, unless you also connect your calendar.</p>
      <ul>
        <li><strong>Google Calendar:</strong> we read the title, time, organiser, attendees and meeting link of your upcoming events. We use this only to decide which meetings to join, following the rules you choose, and to show your upcoming meetings in the app. We do not create, change or delete calendar events.</li>
        <li><strong>Google Meet:</strong> if you turn on joining meetings you start yourself, Google tells us when a Meet call you host begins, with its meeting code, so the note taker can join it. We do not receive the meeting's audio, video or chat from Google.</li>
      </ul>
      <p>Beyric Note Taker's use and transfer of information received from Google APIs to any other app will adhere to the <a href="https://developers.google.com/terms/api-services-user-data-policy">Google API Services User Data Policy</a>, including the Limited Use requirements. In particular:</p>
      <ul>
        <li>We use Google user data only to provide and improve the features described above, which you can see in the app.</li>
        <li>We do not transfer Google user data to others except as needed to provide those features, for security, to comply with the law, or as part of a merger or sale with your notice.</li>
        <li>We do not use Google user data for advertising, and we do not sell it.</li>
        <li>We do not use Google user data to develop, improve or train generalised artificial intelligence or machine learning models.</li>
        <li>People at Beyric do not read Google user data unless you ask us to for support, it is needed for security or to investigate abuse, or the law requires it.</li>
      </ul>
      <p>You can disconnect at any time by revoking access at <a href="https://myaccount.google.com/permissions">myaccount.google.com/permissions</a> or by asking us to remove it. When you disconnect, we stop reading your calendar and delete the calendar data we hold.</p>

      <h2 id="use">How we use your information</h2>
      <ul>
        <li>To provide the service: joining meetings, recording audio, and producing transcripts and notes for you.</li>
        <li>To apply your templates and vocabulary so notes follow your format and spell names correctly.</li>
        <li>To send service emails, such as account verification, password resets, waiting-list and team invitations and notices that an address already has an account, an email when the notes of a meeting you sent the note taker to are ready, and an email to a teammate listing the tasks from a meeting that were assigned to them, and an email with a meeting's notes to the people you choose to share them with. You can turn these emails off for yourself in Settings, under Email preferences, and stop emails to the people your own tasks are assigned to under Tasks.</li>
        <li>To keep the service secure, prevent abuse and fix problems.</li>
        <li>To meet legal obligations and enforce our Terms of Service.</li>
      </ul>
      <p>We do not sell your personal information, and we do not use your meeting content for advertising. We do not use your meeting audio, transcripts or notes to train artificial intelligence models, and our providers do not use them for training either (see below).</p>

      <h2 id="share">Who we share information with</h2>
      <ul>
        <li><strong>OpenAI</strong> turns meeting audio into a transcript, writes notes from it, reads the action items to find who took each one and by when, and checks the notes against the transcript to find names and terms that may have been misheard. Under OpenAI's API terms, data sent through its API is not used to train its models.</li>
        <li><strong>Resend</strong> sends our service emails (account verification, password resets, waiting-list and team invitations, notices that an address already has an account, notes-ready emails and task assignment emails). It receives the email address and, if given, the name on each of those emails, and their content, which includes one-time links and, in a notes-ready email, a short extract of your notes (the summary, decisions, action items and open questions) and, in a task email, the titles and due dates of the tasks assigned to the recipient. In an email of shared notes it delivers to the people you choose the same extract of your notes, the message you wrote, and any PDF, Word or transcript file you attached, in the language you picked.</li>
        <li><strong>Slack</strong>, only if an owner or admin of your team connects it in Settings. After the notes of a meeting you have shared with your team are written, we send one message to the Slack channel the team chose with the meeting&apos;s name, a link to it, and the title and owner name of each task taken from it. Slack is in the United States and is not sent audio, transcripts or notes. The webhook address you paste is encrypted before it is stored, is never shown again, and is deleted when you disconnect.</li>
        <li><strong>Contabo</strong> hosts our server, in France.</li>
        <li><strong>Cloudflare</strong> provides our domain's DNS and may carry traffic to the site.</li>
        <li><strong>Meeting and calendar platforms</strong> (Google and Zoom), as needed to join meetings and to read calendar events you have authorised.</li>
        <li><strong>Authorities</strong>, when required by law or court order, or to protect the rights, safety or property of Beyric, our users or others.</li>
        <li><strong>A buyer or successor</strong>, if Beyric is involved in a merger, acquisition or sale of assets, subject to this policy.</li>
      </ul>
      <p>The software that joins meetings runs on our own server; no other company receives the recording from it.</p>

      <h2 id="recording">Recording and participant notice</h2>
      <p>The note taker appears in the meeting's participant list while it records. Recording laws differ by country and state, and some require the consent of everyone in the conversation.</p>
      <p>If you use Beyric Note Taker, you are responsible for telling participants that the meeting is being recorded and for getting any consent the law requires. Any participant can ask the host to remove the note taker, and recording stops when it leaves.</p>
      <p>If you took part in a meeting recorded by one of our users and want a copy of, or changes to, information about you, contact that user first. You can also write to us and we will help.</p>

      <h2 id="retention">How long we keep information</h2>
      <ul>
        <li>Meeting audio: deleted automatically {days} days after the meeting.</li>
        <li>Transcripts and notes, and the lists of decisions, open questions, Please check items and tasks taken from them: kept until you ask us to delete them or close your account. When a meeting is deleted, everything taken from it is deleted with it: its transcript, notes, translations, the record of who you shared it with, decisions, open questions, Please check items and tasks, and its audio. The hours it counted stay counted.</li>
        <li>Calendar data: we keep only what is needed for upcoming meetings, and delete it when you disconnect your calendar.</li>
        <li>Account information: kept while your account is active and deleted within 30 days of closing it, except where the law requires us to keep records.</li>
        <li>An account whose email address is not confirmed within 48 hours is deleted.</li>
        <li>Waiting list: your email address is kept until you are invited and sign up, or you ask us to remove it.</li>
        <li>Hours counted: when an account is deleted, we keep only the number of recorded hours counted for it, with a one-way fingerprint of the email address, so that signing up again does not reset the pilot's hours limit.</li>
        <li>Mail log: the address, kind and time of each email we send, without its content, for 30 days.</li>
        <li>Do-not-mail list: if an email to an address bounces or is reported as spam, we keep a one-way fingerprint of the address, and not the address itself, so that we do not mail it again.</li>
        <li>Backups: database backups are deleted after 14 days.</li>
      </ul>

      <h2 id="security">Security</h2>
      <p>Traffic to the site is encrypted with TLS. Access to the server is limited to the people who run it, the software that joins meetings runs on a separate network from our database, and secrets are kept out of our code. Google access credentials are encrypted before they are stored, and the encryption key is kept apart from the database and its backups. Our administrator can see account details (name, email, status and hours used) and, if you ask for help signing in, can create a one-time password reset link for your account, which would give access to it. You are notified in the app when this happens, and the action is logged. The administrator's account screens do not show your meetings or notes. No system is completely secure. If a personal data breach affects you, we will tell you and the relevant authorities as the law requires.</p>

      <h2 id="transfers">International transfers</h2>
      <p>Our server is in France. OpenAI may process meeting audio and transcripts, and Resend the email addresses and names it sends mail to, in the United States. When personal data leaves Nigeria, the UK or the European Economic Area, we rely on the safeguards those laws allow, such as standard contractual clauses in our providers' terms.</p>

      <h2 id="legal">Legal bases for processing</h2>
      <p>Where data protection laws such as the Nigeria Data Protection Act 2023, the GDPR or the UK GDPR apply, we rely on:</p>
      <ul>
        <li>Performance of a contract, to provide the service you signed up for.</li>
        <li>Legitimate interests, to secure and maintain the service, where those interests are not overridden by your rights.</li>
        <li>Consent, where we ask for it, such as when you connect your Google account. You can withdraw it at any time.</li>
        <li>Legal obligation, where the law requires us to keep or disclose information.</li>
      </ul>

      <h2 id="rights">Your rights and choices</h2>
      <p>Depending on where you live, you may have the right to access, correct or delete your information, to receive a copy of it, to restrict or object to some processing, to withdraw consent, and to complain to a data protection authority such as the Nigeria Data Protection Commission.</p>
      <p>To use any of these rights, email <a href="mailto:privacy@beyrictech.com">privacy@beyrictech.com</a>. We will reply within 30 days and may need to confirm your identity first.</p>
      <p>Anyone who receives notes you shared can stop all notes email from Beyric Note Taker to their address with the link at the bottom of that email. We then keep a one-way fingerprint of the address so that we do not mail it again.</p>

      <h2 id="children">Children</h2>
      <p>Beyric Note Taker is not meant for children under 16, and we do not knowingly collect their information. If you believe a child has given us personal information, contact us and we will delete it.</p>

      <h2 id="cookies">Cookies</h2>
      <p>We use one strictly necessary cookie to keep you signed in and to protect forms against forgery. We do not use analytics or advertising cookies.</p>

      <h2 id="changes">Changes to this policy</h2>
      <p>We may update this policy from time to time. The date at the top shows when it last changed.</p>

      <h2 id="contact">Contact us</h2>
      <p>Beyrichq LTD, Lagos, Nigeria. Email: <a href="mailto:privacy@beyrictech.com">privacy@beyrictech.com</a>.</p>
    </>
  );
}
