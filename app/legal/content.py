"""English and Italian copy for Calory's public legal pages.

The documents intentionally describe the services currently present in the
codebase. They are operational legal drafts, not a substitute for review by a
lawyer familiar with the operator's entity and launch markets.
"""

LEGAL_DOCUMENTS: dict[tuple[str, str], dict[str, str]] = {
    ("privacy", "en"): {
        "title": "Privacy Policy",
        "description": "How Calory collects, uses and protects personal data.",
        "lead": "A clear account of the data Calory needs to estimate meals, maintain your daily balance and operate subscriptions.",
        "footer": "Awareness without guilt.",
        "body": """
<section>
  <h2>1. Who is responsible</h2>
  <p><strong>{{OPERATOR_NAME}}</strong> operates the Calory mobile application and is the controller of the personal data described in this policy. For privacy questions or requests, contact {{CONTACT}}.</p>
  <p>This policy applies to the Calory app, its backend services and these public legal pages. Third-party services linked from Calory have their own privacy terms.</p>
</section>
<section>
  <h2>2. What Calory collects and how it is collected</h2>
  <p>Calory collects personal data in three ways: (a) directly from information and media you enter or capture in the app, (b) from the identity or store provider you choose when signing in or subscribing, and (c) automatically from authenticated app requests.</p>
  <h3>Account and identity data</h3>
  <ul>
    <li>Firebase user identifier, email address, display name and the Google, Apple or email sign-in provider you choose (collected via Firebase Authentication when you sign in).</li>
    <li>Internal account identifier and onboarding status.</li>
  </ul>
  <h3>Profile and wellness data</h3>
  <ul>
    <li>Your calorie goal, goal type, age, height, weight, formula selection, activity level, preferred units and other settings you enter directly during onboarding or in your profile.</li>
    <li>Daily calorie summaries, consumed and burned calories, water entries, meal categories and progress history generated from the entries you save.</li>
  </ul>
  <h3>Meal and activity content</h3>
  <ul>
    <li>Descriptions you type, meal photos you capture with the camera or select from your photo library, and voice recordings you record with the microphone; transcripts; detected food items; calorie and macro estimates; corrections and confirmations.</li>
    <li>Activities and calories burned that you enter manually.</li>
    <li>Food memories derived from confirmed entries so repeated meals can be recognized more consistently.</li>
  </ul>
  <h3>Subscription, technical and support data</h3>
  <ul>
    <li>Subscription entitlement, product, store, renewal and expiry information received from Apple App Store / Google Play and RevenueCat when you view or purchase a subscription. Calory does not receive your complete payment-card details.</li>
    <li>IP address, request metadata, app version, device and diagnostic information, performance traces and error reports generated automatically when the service is used.</li>
    <li>Messages and information you send when requesting support or exercising a privacy right.</li>
  </ul>
</section>
<section>
  <h2>3. Why Calory uses the data</h2>
  <ul>
    <li><strong>Provide the service:</strong> authenticate you, estimate meals, calculate your balance, synchronize history, store preferences and provide premium features.</li>
    <li><strong>Personalize and improve:</strong> apply your corrections, make repeat estimates more consistent, troubleshoot failures and understand service quality.</li>
    <li><strong>Operate purchases:</strong> validate and restore subscriptions, manage entitlements and prevent billing abuse.</li>
    <li><strong>Protect Calory:</strong> secure accounts and infrastructure, investigate misuse and enforce these terms.</li>
    <li><strong>Meet legal duties:</strong> maintain records or respond to lawful requests where required.</li>
  </ul>
  <p>Depending on where you live, the legal bases are performance of the contract you request, your consent, legitimate interests in operating and securing Calory, and compliance with law. Where profile or meal information qualifies as health data, or when data is transmitted to third-party AI services, Calory relies on your explicit consent. You may withdraw consent at any time in the app Settings or by contacting us, although AI features will then not run until you re-enable permission.</p>
  <p class="note"><strong>No sale or third-party advertising.</strong> Calory does not sell personal data and does not use meal or profile data for third-party targeted advertising.</p>
</section>
<section>
  <h2>4. AI processing and in-app permission</h2>
  <p>Before sending any personal data to a third-party AI service, Calory displays a clear in-app disclosure explaining what data will be sent, who receives it, and asks for your explicit permission. You can grant, review or withdraw this permission at any time in Profile &rarr; Settings.</p>
  <p>When you grant permission and use an AI feature, Calory transmits the meal text description, meal photo or voice recording you submit—together with relevant nutritional context (your daily calorie/macro goals, logged meals for the day or period being reviewed, and saved food memories)—to <strong>OpenRouter (OpenRouter, Inc.)</strong> and the AI model provider selected through it, <strong>Google Gemini (Google LLC)</strong>, solely to transcribe voice notes, recognize food items, estimate calories and macronutrients, and generate personalized meal suggestions and insights. Do not include information about other people or sensitive details that are unnecessary for a meal estimate.</p>
  <p>AI results can be inaccurate. Calory may retain inference inputs, outputs, model identifiers, latency and error diagnostics to provide the feature, investigate quality and keep an audit trail. Access is restricted to operational needs.</p>
  <p>AI estimates do not make decisions that produce legal or similarly significant effects about you.</p>
</section>
<section>
  <h2>5. Who receives data and equal-protection guarantee</h2>
  <p>Calory shares only the data reasonably needed for the following third-party service providers to perform their services:</p>
  <ul>
    <li><strong>Google Firebase (Google LLC)</strong> for authentication, account-token verification and media storage.</li>
    <li><strong>Apple Inc. and Google LLC</strong> when you use their sign-in services or complete an in-app purchase through their stores.</li>
    <li><strong>RevenueCat, Inc.</strong> to manage subscription status, entitlements and purchase events.</li>
    <li><strong>Railway Corp. and PostgreSQL infrastructure</strong> to host the API, databases and background processing.</li>
    <li><strong>OpenRouter (OpenRouter, Inc.) and Google Gemini (Google LLC)</strong> to process meal text descriptions, meal photos, voice recordings and nutritional context for AI food estimation and insights, only after you grant explicit in-app permission.</li>
    <li><strong>Sentry (Functional Software, Inc.)</strong>, when enabled, for error, performance and diagnostic monitoring.</li>
    <li>Professional advisers, authorities or a successor operator where required by law, to protect rights, or as part of a legitimate business transaction.</li>
  </ul>
  <p><strong>Equal protection commitment:</strong> Every third party with whom Calory shares personal data (including OpenRouter, Google Gemini, Google Firebase, RevenueCat and our infrastructure providers) is contractually and legally required to provide the same or equal protection of your personal data as stated in this Privacy Policy and required by applicable data-protection laws and Apple App Store / Google Play guidelines, and is prohibited from using your personal data for unauthorized purposes or third-party advertising.</p>
  <p>Some providers process data in countries outside your own. Calory uses the safeguards required by applicable law, such as adequacy decisions or approved contractual clauses, where relevant.</p>
</section>
<section>
  <h2>6. Retention and deletion</h2>
  <p>Account, profile, meal and progress data are generally kept while your account is active so Calory can provide history and personalized estimates. Subscription and transaction records may be kept for the period required for accounting, fraud prevention and legal compliance. Administrative security audit records are pseudonymized and normally retained for no more than {{AUDIT_RETENTION_DAYS}} days. Identifiers held only to complete a retryable deletion are erased when that deletion completes. Provider backups and other records may remain for a limited period where necessary and are deleted or de-identified when no longer required.</p>
  <p>You may request account and data deletion through the deletion control in Calory when available, or by contacting {{CONTACT}}. Deleting the app from your device does not by itself delete the account. Calory may ask you to verify ownership before acting on a request.</p>
</section>
<section>
  <h2>7. Your choices and rights</h2>
  <p>Depending on your location, you may have rights to access, correct, export, delete, restrict or object to processing, withdraw consent and complain to your local data-protection authority. You can edit many profile and meal details directly in Calory. For any other request, contact {{CONTACT}}.</p>
  <p>You can manage or cancel subscriptions in the Apple App Store or Google Play account used to purchase them. Authentication permissions can also be reviewed in your Apple or Google account.</p>
</section>
<section>
  <h2>8. Security</h2>
  <p>Calory uses access controls, authenticated API requests, encrypted network connections and service-provider security controls designed to protect personal data. No method of storage or transmission is completely secure, so absolute security cannot be guaranteed.</p>
</section>
<section>
  <h2>9. Children</h2>
  <p>Calory is not directed to children who cannot lawfully consent to the processing of their data in their country. A parent or guardian who believes a child provided data without the required authorization should contact {{CONTACT}}.</p>
</section>
<section>
  <h2>10. Changes and contact</h2>
  <p>Calory may update this policy when the product, providers or legal requirements change. The effective date at the top will be revised, and material changes may also be communicated in the app.</p>
  <p>Privacy questions and requests can be sent to {{CONTACT}}. You may also lodge a complaint with the data-protection authority where you live.</p>
</section>
""",
    },
    ("privacy", "it"): {
        "title": "Informativa privacy",
        "description": "Come Calory raccoglie, usa e protegge i dati personali.",
        "lead": "Una spiegazione chiara dei dati necessari a Calory per stimare i pasti, mantenere il bilancio giornaliero e gestire gli abbonamenti.",
        "footer": "Consapevolezza, senza sensi di colpa.",
        "body": """
<section>
  <h2>1. Titolare del trattamento</h2>
  <p><strong>{{OPERATOR_NAME}}</strong> gestisce l’app mobile Calory ed è titolare del trattamento dei dati personali descritti in questa informativa. Per domande o richieste privacy è possibile contattare {{CONTACT}}.</p>
  <p>L’informativa si applica all’app Calory, ai relativi servizi backend e a queste pagine legali pubbliche. I servizi di terze parti collegati da Calory hanno informative proprie.</p>
</section>
<section>
  <h2>2. Dati raccolti e modalità di raccolta</h2>
  <p>Calory raccoglie i dati personali in tre modi: (a) direttamente dalle informazioni e dai contenuti multimediali inseriti o acquisiti dall’utente nell’app, (b) dal provider di identità o dallo store scelto durante l’accesso o l’abbonamento e (c) automaticamente dalle richieste autenticate dell’app.</p>
  <h3>Account e identità</h3>
  <ul>
    <li>Identificativo Firebase, indirizzo email, nome visualizzato e provider Google, Apple o email scelto per l’accesso (raccolti tramite Firebase Authentication al momento dell’accesso).</li>
    <li>Identificativo interno dell’account e stato dell’onboarding.</li>
  </ul>
  <h3>Profilo e dati sul benessere</h3>
  <ul>
    <li>Obiettivo calorico, tipo di obiettivo, età, altezza, peso, formula selezionata, livello di attività, unità preferite e altre impostazioni inserite direttamente durante l’onboarding o nel profilo.</li>
    <li>Riepiloghi giornalieri, calorie assunte e bruciate, registrazioni dell’acqua, categorie dei pasti e cronologia dei progressi generati a partire dalle voci salvate.</li>
  </ul>
  <h3>Contenuti relativi a pasti e attività</h3>
  <ul>
    <li>Descrizioni digitate, foto dei pasti scattate con la fotocamera o selezionate dalla libreria foto e registrazioni vocali effettuate tramite microfono; trascrizioni; alimenti rilevati; stime di calorie e macronutrienti; correzioni e conferme.</li>
    <li>Attività e calorie bruciate inserite manualmente.</li>
    <li>Memorie alimentari ricavate dalle conferme, per riconoscere con maggiore coerenza i pasti ripetuti.</li>
  </ul>
  <h3>Abbonamenti, dati tecnici e assistenza</h3>
  <ul>
    <li>Dati su entitlement, prodotto, store, rinnovo e scadenza ricevuti da Apple App Store / Google Play e RevenueCat. Calory non riceve i dati completi della carta di pagamento.</li>
    <li>Indirizzo IP, metadati delle richieste, versione dell’app, informazioni su dispositivo e diagnostica, tracce di prestazione e segnalazioni di errore generate automaticamente durante l’uso.</li>
    <li>Messaggi e informazioni inviati in caso di assistenza o esercizio di un diritto privacy.</li>
  </ul>
</section>
<section>
  <h2>3. Finalità e basi giuridiche</h2>
  <ul>
    <li><strong>Fornire il servizio:</strong> autenticare l’utente, stimare i pasti, calcolare il bilancio, sincronizzare la cronologia, salvare preferenze e offrire le funzioni premium.</li>
    <li><strong>Personalizzare e migliorare:</strong> applicare le correzioni, rendere più coerenti le stime ricorrenti, risolvere malfunzionamenti e comprendere la qualità del servizio.</li>
    <li><strong>Gestire gli acquisti:</strong> validare e ripristinare gli abbonamenti, gestire gli entitlement e prevenire abusi di fatturazione.</li>
    <li><strong>Proteggere Calory:</strong> tutelare account e infrastruttura, indagare abusi e applicare i termini.</li>
    <li><strong>Adempiere a obblighi di legge:</strong> conservare registrazioni o rispondere a richieste legittime quando necessario.</li>
  </ul>
  <p>A seconda del luogo in cui si trova l’utente, le basi giuridiche sono l’esecuzione del contratto richiesto, il consenso, il legittimo interesse a gestire e proteggere Calory e l’adempimento di obblighi legali. Quando profilo o pasti costituiscono dati relativi alla salute o vengono trasmessi a servizi IA di terze parti, Calory si basa sul consenso esplicito dell’utente. Il consenso può essere revocato in qualsiasi momento nelle Impostazioni dell’app o contattandoci; in tal caso le funzioni IA non invieranno dati finché il permesso non verrà nuovamente concesso.</p>
  <p class="note"><strong>Nessuna vendita o pubblicità di terze parti.</strong> Calory non vende dati personali e non usa i dati di pasti o profilo per pubblicità mirata di terze parti.</p>
</section>
<section>
  <h2>4. Trattamento tramite IA e consenso nell’app</h2>
  <p>Prima di inviare qualsiasi dato personale a un servizio IA di terze parti, Calory mostra una chiara informativa nell’app che spiega quali dati verranno inviati, a chi vengono trasmessi e richiede il permesso esplicito dell’utente. È possibile concedere, consultare o revocare tale permesso in qualsiasi momento in Profilo &rarr; Impostazioni.</p>
  <p>Quando l’utente concede il permesso e usa una funzione IA, Calory trasmette la descrizione testuale del pasto, la foto del pasto o la registrazione vocale inviata—insieme al contesto nutrizionale pertinente (obiettivi giornalieri di calorie/macro, pasti registrati per il giorno o il periodo analizzato e memorie alimentari salvate)—a <strong>OpenRouter (OpenRouter, Inc.)</strong> e al provider del modello IA selezionato tramite esso, <strong>Google Gemini (Google LLC)</strong>, esclusivamente per trascrivere note vocali, riconoscere gli alimenti, stimare calorie e macronutrienti e generare suggerimenti e insight personalizzati. È opportuno non includere dati di altre persone o informazioni sensibili non necessarie alla stima.</p>
  <p>I risultati IA possono essere inesatti. Calory può conservare input, output, identificativi dei modelli, latenza e diagnostica degli errori per fornire la funzione, verificarne la qualità e mantenere una traccia di audit. L’accesso è limitato alle necessità operative.</p>
  <p>Le stime IA non producono decisioni con effetti giuridici o analogamente significativi sull’utente.</p>
</section>
<section>
  <h2>5. Destinatari dei dati e garanzia di pari protezione</h2>
  <p>Calory condivide solo i dati ragionevolmente necessari affinché i seguenti fornitori terzi svolgano i propri servizi:</p>
  <ul>
    <li><strong>Google Firebase (Google LLC)</strong> per autenticazione, verifica dei token e archiviazione media.</li>
    <li><strong>Apple Inc. e Google LLC</strong> per i rispettivi servizi di accesso e per gli acquisti in-app effettuati tramite gli store.</li>
    <li><strong>RevenueCat, Inc.</strong> per stato degli abbonamenti, entitlement ed eventi di acquisto.</li>
    <li><strong>Railway Corp. e infrastruttura PostgreSQL</strong> per API, database ed elaborazioni in background.</li>
    <li><strong>OpenRouter (OpenRouter, Inc.) e Google Gemini (Google LLC)</strong> per elaborare descrizioni testuali, foto, audio dei pasti e contesto nutrizionale per le stime e gli insight IA, esclusivamente dopo il consenso esplicito dell’utente nell’app.</li>
    <li><strong>Sentry (Functional Software, Inc.)</strong>, quando attivo, per monitoraggio di errori, prestazioni e diagnostica.</li>
    <li>Consulenti professionali, autorità o un eventuale operatore subentrante quando richiesto dalla legge, per tutelare diritti o nell’ambito di una legittima operazione societaria.</li>
  </ul>
  <p><strong>Garanzia di pari protezione:</strong> Ogni terza parte con cui Calory condivide dati personali (inclusi OpenRouter, Google Gemini, Google Firebase, RevenueCat e i fornitori di infrastruttura) è vincolata contrattualmente e legalmente a garantire un livello di protezione dei dati personali uguale o equivalente a quello descritto nella presente Informativa privacy e richiesto dalle leggi applicabili sulla protezione dei dati e dalle linee guida di Apple App Store e Google Play, con divieto di utilizzare i dati per finalità non autorizzate o pubblicità di terze parti.</p>
  <p>Alcuni fornitori possono trattare dati in Paesi diversi da quello dell’utente. Quando necessario, Calory applica le garanzie richieste dalla legge, come decisioni di adeguatezza o clausole contrattuali approvate.</p>
</section>
<section>
  <h2>6. Conservazione e cancellazione</h2>
  <p>I dati di account, profilo, pasti e progressi sono generalmente conservati mentre l’account è attivo, per offrire cronologia e stime personalizzate. I dati relativi ad abbonamenti e transazioni possono essere conservati per i periodi necessari a contabilità, prevenzione frodi e obblighi legali. I registri amministrativi di sicurezza sono pseudonimizzati e normalmente conservati per non più di {{AUDIT_RETENTION_DAYS}} giorni. Gli identificativi necessari soltanto a completare una cancellazione ripetibile vengono eliminati al suo completamento. Backup dei fornitori e altri registri possono permanere per un periodo limitato quando necessario e vengono eliminati o de-identificati quando non più richiesti.</p>
  <p>È possibile richiedere la cancellazione dell’account e dei dati tramite il comando disponibile in Calory, quando presente, oppure contattando {{CONTACT}}. Disinstallare l’app non cancella automaticamente l’account. Prima di procedere Calory può chiedere di verificarne la titolarità.</p>
</section>
<section>
  <h2>7. Scelte e diritti</h2>
  <p>A seconda del luogo in cui si trova, l’utente può avere diritto ad accesso, rettifica, portabilità, cancellazione, limitazione, opposizione, revoca del consenso e reclamo all’autorità di controllo competente. Molti dati di profilo e pasto possono essere modificati direttamente in Calory. Per altre richieste è possibile contattare {{CONTACT}}.</p>
  <p>Gli abbonamenti possono essere gestiti o annullati nell’account App Store o Google Play usato per l’acquisto. Le autorizzazioni di accesso possono essere riviste anche nell’account Apple o Google.</p>
</section>
<section>
  <h2>8. Sicurezza</h2>
  <p>Calory usa controlli di accesso, richieste API autenticate, connessioni di rete cifrate e misure di sicurezza dei fornitori per proteggere i dati personali. Nessun sistema di archiviazione o trasmissione è completamente sicuro e non è quindi possibile garantire una sicurezza assoluta.</p>
</section>
<section>
  <h2>9. Minori</h2>
  <p>Calory non è destinata a minori che, nel proprio Paese, non possono prestare validamente il consenso al trattamento. Un genitore o tutore che ritenga siano stati forniti dati senza l’autorizzazione necessaria può contattare {{CONTACT}}.</p>
</section>
<section>
  <h2>10. Modifiche e contatti</h2>
  <p>Calory può aggiornare l’informativa quando cambiano prodotto, fornitori o requisiti legali. La data in alto verrà aggiornata e le modifiche sostanziali potranno essere comunicate anche nell’app.</p>
  <p>Domande e richieste privacy possono essere inviate a {{CONTACT}}. È inoltre possibile presentare reclamo all’autorità di protezione dati competente.</p>
</section>
""",
    },
    ("terms", "en"): {
        "title": "Terms & Conditions",
        "description": "The terms governing access to and use of Calory.",
        "lead": "The rules that keep Calory useful, safe and transparent—including how AI estimates and subscriptions work.",
        "footer": "Made for calm, everyday awareness.",
        "body": """
<section>
  <h2>1. Agreement and provider</h2>
  <p>These Terms &amp; Conditions form an agreement between you and <strong>{{OPERATOR_NAME}}</strong>, the operator of Calory. By creating an account, accessing the app or purchasing Calory Pro, you agree to these terms and the Privacy Policy. If you do not agree, do not use Calory.</p>
  <p>You must have legal capacity to enter this agreement. If local law requires parental or guardian authorization, you may use Calory only with that authorization.</p>
</section>
<section>
  <h2>2. What Calory provides</h2>
  <p>Calory is a calorie-awareness tool that lets you record meals using text, photos or voice, estimate calories and macronutrients with AI, record activity and water, and review daily or historical balances. Features may differ by platform, country, app version and subscription tier.</p>
  <p>Calory may change, improve, suspend or discontinue features. Where a change materially affects a paid service, Calory will provide notice or remedies required by applicable law.</p>
</section>
<section>
  <h2>3. Estimates—not medical advice</h2>
  <p>Food recognition, calorie targets, macronutrients, activity values, insights and other outputs are estimates. They may be incomplete or inaccurate, particularly when portions, ingredients or preparation methods are unclear.</p>
  <p>Calory is not a medical device and does not provide medical, dietary or clinical advice. It is not intended to diagnose, treat or prevent any condition and must not be used for emergencies. Consult a qualified professional before making decisions where health risks, pregnancy, an eating disorder, allergies, medication or a medical condition may be involved.</p>
  <p>You remain responsible for reviewing estimates and deciding whether they are appropriate for you.</p>
</section>
<section>
  <h2>4. Accounts</h2>
  <p>You sign in through supported identity providers such as Apple or Google. You are responsible for keeping that account secure, providing accurate information and notifying Calory of suspected unauthorized access. You may not impersonate others, share access unlawfully or create accounts to evade restrictions.</p>
</section>
<section>
  <h2>5. Calory Pro subscriptions</h2>
  <ul>
    <li>The price, billing period, trial or introductory offer, included features and renewal terms are shown on the purchase screen before you confirm.</li>
    <li>Subscriptions are processed by Apple App Store or Google Play and managed through RevenueCat. Payment is charged to the store account you use.</li>
    <li>Unless the purchase screen states otherwise, subscriptions renew automatically until cancelled. Cancel through your App Store or Google Play subscription settings before the renewal deadline shown by the store.</li>
    <li>Deleting Calory or your Calory account does not automatically cancel a store subscription.</li>
    <li>Refunds, billing disputes, trial conversion and cancellation timing are governed by the applicable store rules and mandatory consumer law.</li>
  </ul>
  <p>Calory may change future pricing or plan contents with the notice and consent required by the store and applicable law. Previously purchased rights and mandatory consumer protections remain unaffected.</p>
</section>
<section>
  <h2>6. Your content</h2>
  <p>You retain ownership of meal descriptions, photos, audio, corrections and other content you submit. You grant Calory a limited, worldwide, non-exclusive licence to host, reproduce, transform and process that content only as needed to provide, secure and improve the service, including through the providers identified in the Privacy Policy.</p>
  <p>You confirm that you have the right to submit the content and that it does not unlawfully reveal another person’s private information or infringe intellectual-property or other rights.</p>
</section>
<section>
  <h2>7. Acceptable use</h2>
  <p>You may not:</p>
  <ul>
    <li>use Calory for unlawful, harmful, fraudulent or abusive activity;</li>
    <li>upload malware or content that violates another person’s rights;</li>
    <li>attempt to bypass authentication, subscription controls, rate limits or security measures;</li>
    <li>scrape, reverse engineer or interfere with Calory except where applicable law expressly permits it;</li>
    <li>use automated systems in a way that degrades the service or creates unreasonable cost.</li>
  </ul>
</section>
<section>
  <h2>8. Calory property and third-party services</h2>
  <p>The Calory name, brand, interface, software and original content are owned by {{OPERATOR_NAME}} or its licensors. These terms grant only a personal, limited, revocable, non-transferable right to use the app.</p>
  <p>Calory depends on third-party services including identity providers, app stores, RevenueCat, hosting, storage and AI providers. Their separate terms may apply, and temporary failures or changes outside Calory’s reasonable control can affect availability.</p>
</section>
<section>
  <h2>9. Availability, disclaimers and liability</h2>
  <p>Calory is provided with reasonable care and skill, but continuous or error-free operation is not guaranteed. To the maximum extent permitted by law, Calory is provided “as is” and “as available,” without warranties beyond those that cannot legally be excluded.</p>
  <p>To the maximum extent permitted by law, {{OPERATOR_NAME}} is not liable for indirect or consequential losses, loss of data or decisions made in reliance on an AI estimate. Nothing in these terms excludes liability that cannot lawfully be limited, or any mandatory rights you have as a consumer.</p>
</section>
<section>
  <h2>10. Suspension, termination and deletion</h2>
  <p>You may stop using Calory at any time and request deletion as explained in the Privacy Policy. Suspension or termination is separate from account deletion. Calory may proportionately restrict access where reasonably necessary to protect users or infrastructure, comply with law, address fraud or respond to a serious or repeated breach of these terms. Depending on urgency, restrictions may be immediate. Where required or reasonably possible, Calory will provide the reason, duration, subscription consequences and a way to request review through {{CONTACT}}.</p>
  <p>Removing promotional access does not cancel an Apple App Store or Google Play subscription. If access to a paid service is restricted, billing, cancellation and any refund remain subject to the applicable store rules and mandatory consumer law.</p>
  <p>Terms that by their nature should survive termination—including ownership, disclaimers and accrued payment obligations—continue to apply.</p>
</section>
<section>
  <h2>11. Governing law and changes</h2>
  <p>These terms are governed by Italian law, without depriving consumers of mandatory protections or the courts available to them under the law of their country of residence.</p>
  <p>Calory may update these terms for legal, security or product reasons. The effective date will change and material updates may be communicated in the app. Continued use after an update takes effect constitutes acceptance where permitted by law; otherwise Calory will request consent.</p>
</section>
<section>
  <h2>12. Contact</h2>
  <p>Questions about these terms can be sent to {{CONTACT}}.</p>
</section>
""",
    },
    ("terms", "it"): {
        "title": "Termini e condizioni",
        "description": "I termini che regolano l’accesso e l’utilizzo di Calory.",
        "lead": "Le regole che rendono Calory utile, sicura e trasparente, incluso il funzionamento delle stime AI e degli abbonamenti.",
        "footer": "Pensata per una consapevolezza quotidiana e serena.",
        "body": """
<section>
  <h2>1. Accordo e fornitore</h2>
  <p>I presenti Termini e condizioni costituiscono un accordo tra l’utente e <strong>{{OPERATOR_NAME}}</strong>, gestore di Calory. Creando un account, accedendo all’app o acquistando Calory Pro, l’utente accetta questi termini e l’Informativa privacy. Se non li accetta, non deve usare Calory.</p>
  <p>L’utente deve avere la capacità giuridica necessaria per concludere il presente accordo. Se la legge locale richiede l’autorizzazione di un genitore o tutore, Calory può essere usata solo con tale autorizzazione.</p>
</section>
<section>
  <h2>2. Il servizio Calory</h2>
  <p>Calory è uno strumento di consapevolezza calorica che consente di registrare pasti tramite testo, foto o voce, stimare calorie e macronutrienti con l’AI, aggiungere attività e acqua e consultare bilanci giornalieri o storici. Le funzioni possono variare in base a piattaforma, Paese, versione e piano di abbonamento.</p>
  <p>Calory può modificare, migliorare, sospendere o interrompere funzioni. Se una modifica incide in modo sostanziale su un servizio a pagamento, verranno forniti il preavviso o i rimedi richiesti dalla legge applicabile.</p>
</section>
<section>
  <h2>3. Stime, non consulenza medica</h2>
  <p>Riconoscimento degli alimenti, obiettivi calorici, macronutrienti, valori delle attività, insight e altri risultati sono stime. Possono essere incompleti o inesatti, soprattutto quando porzioni, ingredienti o preparazione non sono chiari.</p>
  <p>Calory non è un dispositivo medico e non fornisce consulenza medica, dietetica o clinica. Non è destinata a diagnosticare, trattare o prevenire patologie e non deve essere usata in emergenza. È necessario consultare un professionista qualificato prima di decisioni che coinvolgano rischi per la salute, gravidanza, disturbi alimentari, allergie, farmaci o condizioni mediche.</p>
  <p>L’utente resta responsabile di verificare le stime e decidere se siano adeguate alle proprie esigenze.</p>
</section>
<section>
  <h2>4. Account</h2>
  <p>L’accesso avviene tramite provider supportati, come Apple o Google. L’utente è responsabile della sicurezza del relativo account, dell’accuratezza delle informazioni fornite e della segnalazione di accessi sospetti. Non è consentito impersonare altri, condividere illecitamente l’accesso o creare account per eludere restrizioni.</p>
</section>
<section>
  <h2>5. Abbonamenti Calory Pro</h2>
  <ul>
    <li>Prezzo, periodo di fatturazione, eventuale prova o offerta introduttiva, funzioni incluse e condizioni di rinnovo sono mostrati nella schermata di acquisto prima della conferma.</li>
    <li>Gli abbonamenti sono elaborati da Apple App Store o Google Play e gestiti tramite RevenueCat. L’importo viene addebitato all’account dello store usato per l’acquisto.</li>
    <li>Salvo diversa indicazione nella schermata di acquisto, l’abbonamento si rinnova automaticamente fino alla cancellazione. La cancellazione va effettuata nelle impostazioni abbonamenti di App Store o Google Play entro il termine indicato dallo store.</li>
    <li>Disinstallare Calory o cancellare l’account Calory non annulla automaticamente l’abbonamento nello store.</li>
    <li>Rimborsi, contestazioni, conversione delle prove e tempistiche di annullamento seguono le regole dello store e la normativa inderogabile a tutela dei consumatori.</li>
  </ul>
  <p>Calory può modificare prezzi futuri o contenuti dei piani con il preavviso e il consenso richiesti dallo store e dalla legge. Restano salvi i diritti già acquistati e le tutele inderogabili del consumatore.</p>
</section>
<section>
  <h2>6. Contenuti dell’utente</h2>
  <p>L’utente mantiene la titolarità di descrizioni, foto, audio, correzioni e altri contenuti inviati. Concede a Calory una licenza limitata, mondiale, non esclusiva per ospitare, riprodurre, trasformare e trattare tali contenuti solo quanto necessario a fornire, proteggere e migliorare il servizio, anche tramite i fornitori indicati nell’Informativa privacy.</p>
  <p>L’utente conferma di avere il diritto di inviare i contenuti e che questi non rivelino illecitamente informazioni private di terzi né violino proprietà intellettuale o altri diritti.</p>
</section>
<section>
  <h2>7. Uso consentito</h2>
  <p>Non è consentito:</p>
  <ul>
    <li>usare Calory per attività illegali, dannose, fraudolente o abusive;</li>
    <li>caricare malware o contenuti che violino diritti altrui;</li>
    <li>tentare di aggirare autenticazione, controlli dell’abbonamento, limiti o misure di sicurezza;</li>
    <li>estrarre dati, effettuare reverse engineering o interferire con Calory, salvo quanto espressamente consentito dalla legge;</li>
    <li>usare sistemi automatizzati in modo da degradare il servizio o creare costi irragionevoli.</li>
  </ul>
</section>
<section>
  <h2>8. Proprietà di Calory e servizi terzi</h2>
  <p>Nome, marchio, interfaccia, software e contenuti originali di Calory appartengono a {{OPERATOR_NAME}} o ai suoi licenzianti. I presenti termini concedono esclusivamente un diritto personale, limitato, revocabile e non trasferibile di usare l’app.</p>
  <p>Calory dipende da servizi terzi, tra cui provider di identità, store, RevenueCat, hosting, archiviazione e AI. Possono applicarsi i loro termini e guasti o modifiche temporanee fuori dal ragionevole controllo di Calory possono incidere sulla disponibilità.</p>
</section>
<section>
  <h2>9. Disponibilità, garanzie e responsabilità</h2>
  <p>Calory è fornita con ragionevole diligenza professionale, ma non è garantito un funzionamento continuo o privo di errori. Nei limiti consentiti dalla legge, Calory è fornita “così com’è” e “come disponibile”, senza garanzie ulteriori rispetto a quelle che non possono essere escluse.</p>
  <p>Nei limiti consentiti dalla legge, {{OPERATOR_NAME}} non risponde di danni indiretti o consequenziali, perdita di dati o decisioni assunte facendo affidamento su una stima AI. Nulla limita responsabilità che non possono essere escluse per legge o diritti inderogabili del consumatore.</p>
</section>
<section>
  <h2>10. Sospensione, recesso e cancellazione</h2>
  <p>L’utente può interrompere l’uso in qualsiasi momento e richiedere la cancellazione come indicato nell’Informativa privacy. La sospensione o cessazione dell’accesso è distinta dalla cancellazione dell’account. Calory può limitare proporzionalmente l’accesso quando ragionevolmente necessario per proteggere utenti o infrastruttura, adempiere alla legge, contrastare frodi o rispondere a violazioni gravi o ripetute. In base all’urgenza, la limitazione può essere immediata. Quando richiesto o ragionevolmente possibile, Calory comunica motivo, durata, conseguenze sull’abbonamento e modalità per chiedere riesame tramite {{CONTACT}}.</p>
  <p>La rimozione di un accesso promozionale non annulla un abbonamento Apple App Store o Google Play. Se viene limitato l’accesso a un servizio a pagamento, fatturazione, cancellazione ed eventuale rimborso restano soggetti alle regole dello store e alle norme imperative a tutela del consumatore.</p>
  <p>Le clausole che per loro natura devono sopravvivere, incluse titolarità, limitazioni e obblighi di pagamento maturati, continuano ad applicarsi.</p>
</section>
<section>
  <h2>11. Legge applicabile e modifiche</h2>
  <p>I presenti termini sono regolati dalla legge italiana, senza privare il consumatore delle tutele inderogabili o dei fori disponibili secondo la legge del proprio Paese di residenza.</p>
  <p>Calory può aggiornare i termini per ragioni legali, di sicurezza o di prodotto. La data di efficacia verrà aggiornata e le modifiche sostanziali potranno essere comunicate nell’app. L’uso successivo all’entrata in vigore costituisce accettazione quando consentito; negli altri casi verrà richiesto il consenso.</p>
</section>
<section>
  <h2>12. Contatti</h2>
  <p>Le domande relative ai presenti termini possono essere inviate a {{CONTACT}}.</p>
</section>
""",
    },
    ("sources", "en"): {
        "title": "Scientific Sources & Citations",
        "description": "Formulas, medical literature, and evidence base for Calry's calorie and nutrition estimates.",
        "lead": "A transparent overview of the scientific equations, dietary guidelines, and clinical literature used in Calry.",
        "footer": "Evidence-based wellness.",
        "body": """
<section>
  <h2>Medical & Health Disclaimer</h2>
  <p>Calry is designed for nutritional awareness, general wellness, and educational purposes only. Calry is not a medical app or medical device, and does not provide clinical or medical advice, diagnosis, or treatment. It must not be used to replace professional medical consultations. Always consult a qualified physician or registered dietitian before making substantial changes to your diet, especially if you have chronic medical conditions, are pregnant or breastfeeding, or have a history of disordered eating.</p>
</section>
<section>
  <h2>1. Resting Energy Expenditure (BMR) & Daily Calorie Estimation</h2>
  <p>To establish your estimated daily calorie starting point, Calry calculates your Basal Metabolic Rate (BMR) using the Mifflin-St Jeor equation. Widely validated in nutritional science and recommended by the Academy of Nutrition and Dietetics, this equation accounts for body weight, height, age, and biological sex:</p>
  <ul>
    <li><strong>Males:</strong> BMR (kcal/day) = 10 &times; weight (kg) + 6.25 &times; height (cm) &minus; 5 &times; age (years) + 5</li>
    <li><strong>Females:</strong> BMR (kcal/day) = 10 &times; weight (kg) + 6.25 &times; height (cm) &minus; 5 &times; age (years) &minus; 161</li>
  </ul>
  <p><strong>Citation:</strong> Mifflin, M. D., St Jeor, S. T., Hill, L. A., Scott, B. J., Daugherty, S. A., &amp; Koh, Y. O. (1990). A new predictive equation for resting energy expenditure in healthy individuals. <em>The American Journal of Clinical Nutrition</em>, 51(2), 241–247. <a href="https://pubmed.ncbi.nlm.nih.gov/2305711/" target="_blank" rel="noopener noreferrer">PubMed: 2305711</a> &middot; <a href="https://doi.org/10.1093/ajcn/51.2.241" target="_blank" rel="noopener noreferrer">DOI: 10.1093/ajcn/51.2.241</a></p>
</section>
<section>
  <h2>2. Physical Activity Level (PAL) & Total Daily Energy Expenditure (TDEE)</h2>
  <p>Your Total Daily Energy Expenditure is estimated by scaling BMR with Physical Activity Level (PAL) factors derived from international nutritional consensus:</p>
  <ul>
    <li><strong>Sedentary / Low activity:</strong> 1.30 &times; BMR</li>
    <li><strong>Light activity:</strong> 1.40 &times; BMR</li>
    <li><strong>Moderate activity:</strong> 1.50 &times; BMR</li>
    <li><strong>High activity:</strong> 1.60 &times; BMR</li>
  </ul>
  <p><strong>Citation:</strong> Food and Agriculture Organization (FAO), World Health Organization (WHO), &amp; United Nations University (UNU). (2004). <em>Human energy requirements: Report of a Joint FAO/WHO/UNU Expert Consultation</em>. FAO Food and Nutrition Technical Report Series, No. 1. Rome: FAO. <a href="https://www.fao.org/3/y5686e/y5686e07.htm" target="_blank" rel="noopener noreferrer">FAO Technical Report</a></p>
</section>
<section>
  <h2>3. Macronutrient Distribution Ranges (AMDR) & Protein Guidance</h2>
  <p>Daily macronutrient targets are configured according to the Acceptable Macronutrient Distribution Ranges (AMDR) defined by the Institute of Medicine (National Academies of Sciences, Engineering, and Medicine):</p>
  <ul>
    <li><strong>Carbohydrates:</strong> 45% &ndash; 65% of total caloric intake.</li>
    <li><strong>Dietary Fats:</strong> 20% &ndash; 35% of total caloric intake.</li>
    <li><strong>Protein:</strong> 10% &ndash; 35% of total caloric intake, targeted at 1.2 to 1.6 g per kilogram of body weight to support satiety and preserve lean muscle mass during weight management.</li>
  </ul>
  <p><strong>Citations:</strong></p>
  <ul>
    <li>Institute of Medicine. (2005). <em>Dietary Reference Intakes for Energy, Carbohydrate, Fiber, Fat, Fatty Acids, Cholesterol, Protein, and Amino Acids</em>. Washington, DC: The National Academies Press. <a href="https://nap.nationalacademies.org/catalog/10490/" target="_blank" rel="noopener noreferrer">National Academies Catalog</a></li>
    <li>Leidy, H. J., Clifton, P. M., Astrup, A., et al. (2015). The role of protein in weight management and satiety. <em>The American Journal of Clinical Nutrition</em>, 101(6), 1320S–1329S. <a href="https://pubmed.ncbi.nlm.nih.gov/25926512/" target="_blank" rel="noopener noreferrer">PubMed: 25926512</a></li>
    <li>J&auml;ger, R., Kerksick, C. M., Campbell, B. I., et al. (2017). International Society of Sports Nutrition Position Stand: protein and exercise. <em>Journal of the International Society of Sports Nutrition</em>, 14, 20. <a href="https://pubmed.ncbi.nlm.nih.gov/28642676/" target="_blank" rel="noopener noreferrer">PubMed: 28642676</a></li>
  </ul>
</section>
<section>
  <h2>4. Safe Intake Limits & Minimum Calorie Thresholds</h2>
  <p>Calry incorporates minimum safe intake floors (1,200 kcal/day for women and 1,500 kcal/day for men) unless explicitly overridden by the user. These safety boundaries align with medical consensus to prevent malnutrition, micronutrient deficiencies, and adverse metabolic impacts.</p>
  <p><strong>Citations:</strong></p>
  <ul>
    <li>Harvard Health Publishing. (2020). Calorie counting made easy. <em>Harvard Medical School</em>. <a href="https://www.health.harvard.edu/staying-healthy/calorie-counting-made-easy" target="_blank" rel="noopener noreferrer">Harvard Health</a></li>
    <li>U.S. Department of Agriculture &amp; U.S. Department of Health and Human Services. <em>Dietary Guidelines for Americans, 2020-2025</em>. 9th Edition. <a href="https://www.dietaryguidelines.gov/" target="_blank" rel="noopener noreferrer">Dietary Guidelines for Americans</a></li>
  </ul>
</section>
""",
    },
    ("sources", "it"): {
        "title": "Fonti scientifiche e citazioni",
        "description": "Formule, letteratura medica e basi scientifiche delle stime di calorie e nutrizione in Calry.",
        "lead": "Una panoramica trasparente delle equazioni scientifiche, linee guida dietetiche e letteratura clinica adottate da Calry.",
        "footer": "Consapevolezza basata sull'evidenza.",
        "body": """
<section>
  <h2>Avvertenza medica e sulla salute</h2>
  <p>Calry è concepita esclusivamente per scopi educativi, di consapevolezza nutrizionale e di benessere generale. Calry non è un dispositivo medico e non fornisce diagnosi, terapie o consulenze mediche o dietetiche. Non sostituisce il parere di un medico. Consulta sempre un medico o un biologo nutrizionista prima di intraprendere cambiamenti significativi nella tua alimentazione, soprattutto in presenza di condizioni cliniche croniche, gravidanza, allattamento o disturbi del comportamento alimentare.</p>
</section>
<section>
  <h2>1. Metabolismo Basale (BMR) e Fabbisogno Calorico Giornaliero</h2>
  <p>Per determinare il punto di partenza calorico stimato, Calry calcola il Metabolismo Basale (BMR) mediante l'equazione di Mifflin-St Jeor. Ampiamente validata nella letteratura nutrizionale e raccomandata dall'Academy of Nutrition and Dietetics, la formula considera peso, altezza, età e sesso biologico:</p>
  <ul>
    <li><strong>Uomini:</strong> BMR (kcal/giorno) = 10 &times; peso (kg) + 6,25 &times; altezza (cm) &minus; 5 &times; età (anni) + 5</li>
    <li><strong>Donne:</strong> BMR (kcal/giorno) = 10 &times; peso (kg) + 6,25 &times; altezza (cm) &minus; 5 &times; età (anni) &minus; 161</li>
  </ul>
  <p><strong>Citazione:</strong> Mifflin, M. D., St Jeor, S. T., Hill, L. A., Scott, B. J., Daugherty, S. A., &amp; Koh, Y. O. (1990). A new predictive equation for resting energy expenditure in healthy individuals. <em>The American Journal of Clinical Nutrition</em>, 51(2), 241–247. <a href="https://pubmed.ncbi.nlm.nih.gov/2305711/" target="_blank" rel="noopener noreferrer">PubMed: 2305711</a> &middot; <a href="https://doi.org/10.1093/ajcn/51.2.241" target="_blank" rel="noopener noreferrer">DOI: 10.1093/ajcn/51.2.241</a></p>
</section>
<section>
  <h2>2. Livello di Attività Fisica (PAL) e Dispendio Energetico Totale (TDEE)</h2>
  <p>Il dispendio energetico giornaliero totale (TDEE) viene stimato moltiplicando il BMR per i coefficienti del Livello di Attività Fisica (PAL) definiti dal consenso internazionale FAO/OMS:</p>
  <ul>
    <li><strong>Sedentario / Bassa attività:</strong> 1,30 &times; BMR</li>
    <li><strong>Attività leggera:</strong> 1,40 &times; BMR</li>
    <li><strong>Attività moderata:</strong> 1,50 &times; BMR</li>
    <li><strong>Attività intensa:</strong> 1,60 &times; BMR</li>
  </ul>
  <p><strong>Citazione:</strong> Food and Agriculture Organization (FAO), World Health Organization (WHO), &amp; United Nations University (UNU). (2004). <em>Human energy requirements: Report of a Joint FAO/WHO/UNU Expert Consultation</em>. FAO Food and Nutrition Technical Report Series, No. 1. Rome: FAO. <a href="https://www.fao.org/3/y5686e/y5686e07.htm" target="_blank" rel="noopener noreferrer">Rapporto Tecnico FAO</a></p>
</section>
<section>
  <h2>3. Ripartizione dei Macronutrienti (AMDR) e Fabbisogno Proteico</h2>
  <p>La distribuzione dei macronutrienti rispetta gli intervalli di riferimento (AMDR) dell'Institute of Medicine (National Academies of Sciences, Engineering, and Medicine):</p>
  <ul>
    <li><strong>Carboidrati:</strong> 45% &ndash; 65% delle calorie giornaliere.</li>
    <li><strong>Grassi:</strong> 20% &ndash; 35% delle calorie giornaliere.</li>
    <li><strong>Proteine:</strong> 10% &ndash; 35% delle calorie giornaliere, calibrate a 1,2–1,6 g per chilogrammo di peso corporeo per favorire la sazietà e preservare la massa muscolare magra durante la gestione del peso.</li>
  </ul>
  <p><strong>Citazioni:</strong></p>
  <ul>
    <li>Institute of Medicine. (2005). <em>Dietary Reference Intakes for Energy, Carbohydrate, Fiber, Fat, Fatty Acids, Cholesterol, Protein, and Amino Acids</em>. Washington, DC: The National Academies Press. <a href="https://nap.nationalacademies.org/catalog/10490/" target="_blank" rel="noopener noreferrer">Catalogo National Academies</a></li>
    <li>Leidy, H. J., Clifton, P. M., Astrup, A., et al. (2015). The role of protein in weight management and satiety. <em>The American Journal of Clinical Nutrition</em>, 101(6), 1320S–1329S. <a href="https://pubmed.ncbi.nlm.nih.gov/25926512/" target="_blank" rel="noopener noreferrer">PubMed: 25926512</a></li>
    <li>J&auml;ger, R., Kerksick, C. M., Campbell, B. I., et al. (2017). International Society of Sports Nutrition Position Stand: protein and exercise. <em>Journal of the International Society of Sports Nutrition</em>, 14, 20. <a href="https://pubmed.ncbi.nlm.nih.gov/28642676/" target="_blank" rel="noopener noreferrer">PubMed: 28642676</a></li>
  </ul>
</section>
<section>
  <h2>4. Soglie Minime di Sicurezza Nutrizionale</h2>
  <p>Calry adotta soglie minime di sicurezza calorica (almeno 1.200 kcal/giorno per le donne e 1.500 kcal/giorno per gli uomini) a meno che non vi sia una conferma esplicita dell'utente, prevenendo deficit eccessivi che espongono al rischio di carenze nutrizionali.</p>
  <p><strong>Citazioni:</strong></p>
  <ul>
    <li>Harvard Health Publishing. (2020). Calorie counting made easy. <em>Harvard Medical School</em>. <a href="https://www.health.harvard.edu/staying-healthy/calorie-counting-made-easy" target="_blank" rel="noopener noreferrer">Harvard Health</a></li>
    <li>U.S. Department of Agriculture &amp; U.S. Department of Health and Human Services. <em>Dietary Guidelines for Americans, 2020-2025</em>. 9th Edition. <a href="https://www.dietaryguidelines.gov/" target="_blank" rel="noopener noreferrer">Linee Guida Nutrizionali Statunitensi</a></li>
  </ul>
</section>
""",
    },
}
