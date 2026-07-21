"""English and Italian copy for Calry's public legal pages.

The documents intentionally describe the services currently present in the
codebase. They are operational legal drafts, not a substitute for review by a
lawyer familiar with the operator's entity and launch markets.
"""

LEGAL_DOCUMENTS: dict[tuple[str, str], dict[str, str]] = {
    ("privacy", "en"): {
        "title": "Privacy Policy",
        "description": "How Calry collects, uses and protects personal data.",
        "lead": "A clear account of the data Calry needs to estimate meals, maintain your daily balance and operate subscriptions.",
        "footer": "Awareness without guilt.",
        "body": """
<section>
  <h2>1. Who is responsible</h2>
  <p><strong>{{OPERATOR_NAME}}</strong> operates the Calry mobile application and is the controller of the personal data described in this policy. For privacy questions or requests, contact {{CONTACT}}.</p>
  <p>This policy applies to the Calry app, its backend services and these public legal pages. Third-party services linked from Calry have their own privacy terms.</p>
</section>
<section>
  <h2>2. What Calry collects</h2>
  <h3>Account and identity data</h3>
  <ul>
    <li>Firebase user identifier, email address, display name and the Google or Apple sign-in provider you choose.</li>
    <li>Internal account identifier and onboarding status.</li>
  </ul>
  <h3>Profile and wellness data</h3>
  <ul>
    <li>Your calorie goal, goal type, age, height, weight, formula selection, activity level, preferred units and other settings you provide.</li>
    <li>Daily calorie summaries, consumed and burned calories, water entries, meal categories and progress history.</li>
  </ul>
  <h3>Meal and activity content</h3>
  <ul>
    <li>Descriptions, photos and voice recordings you submit; transcripts; detected food items; calorie and macro estimates; corrections and confirmations.</li>
    <li>Activities and calories burned that you enter manually.</li>
    <li>Food memories derived from confirmed entries so repeated meals can be recognized more consistently.</li>
  </ul>
  <h3>Subscription, technical and support data</h3>
  <ul>
    <li>Subscription entitlement, product, store, renewal and expiry information received from RevenueCat. Calry does not receive your complete payment-card details.</li>
    <li>IP address, request metadata, app version, device and diagnostic information, performance traces and error reports generated when the service is used.</li>
    <li>Messages and information you send when requesting support or exercising a privacy right.</li>
  </ul>
</section>
<section>
  <h2>3. Why Calry uses the data</h2>
  <ul>
    <li><strong>Provide the service:</strong> authenticate you, estimate meals, calculate your balance, synchronize history, store preferences and provide premium features.</li>
    <li><strong>Personalize and improve:</strong> apply your corrections, make repeat estimates more consistent, troubleshoot failures and understand service quality.</li>
    <li><strong>Operate purchases:</strong> validate and restore subscriptions, manage entitlements and prevent billing abuse.</li>
    <li><strong>Protect Calry:</strong> secure accounts and infrastructure, investigate misuse and enforce these terms.</li>
    <li><strong>Meet legal duties:</strong> maintain records or respond to lawful requests where required.</li>
  </ul>
  <p>Depending on where you live, the legal bases are performance of the contract you request, your consent, legitimate interests in operating and securing Calry, and compliance with law. Where profile or meal information qualifies as health data, Calry relies on your explicit consent or another basis permitted by applicable law. You may withdraw consent prospectively, although some features will then no longer work.</p>
  <p class="note"><strong>No sale or third-party advertising.</strong> Calry does not sell personal data and does not use meal or profile data for third-party targeted advertising.</p>
</section>
<section>
  <h2>4. AI processing</h2>
  <p>Calry uses artificial intelligence to analyze the text, photo or audio you choose to submit. Relevant content and technical instructions are sent to OpenRouter and the model provider selected through it (currently Google Gemini) to produce an estimate or transcript. Do not include information about other people or sensitive details that are unnecessary for a meal estimate.</p>
  <p>AI results can be inaccurate. Calry may retain inference inputs, outputs, model identifiers, latency and error diagnostics to provide the feature, investigate quality and keep an audit trail. Access is restricted to operational needs.</p>
  <p>AI estimates do not make decisions that produce legal or similarly significant effects about you.</p>
</section>
<section>
  <h2>5. Who receives data</h2>
  <p>Calry shares only the data reasonably needed for the following providers to perform their services:</p>
  <ul>
    <li><strong>Google Firebase</strong> for authentication and account-token verification.</li>
    <li><strong>Apple and Google</strong> when you use their sign-in services or complete an in-app purchase through their stores.</li>
    <li><strong>RevenueCat</strong> to manage subscription status, entitlements and purchase events.</li>
    <li><strong>Railway and PostgreSQL infrastructure</strong> to host the API, databases and background processing.</li>
    <li><strong>The configured media-storage provider</strong> to store meal photos and voice recordings.</li>
    <li><strong>OpenRouter and routed AI model providers</strong> to process meal text, photos and audio.</li>
    <li><strong>Sentry</strong>, when enabled, for error, performance and diagnostic monitoring.</li>
    <li>Professional advisers, authorities or a successor operator where required by law, to protect rights, or as part of a legitimate business transaction.</li>
  </ul>
  <p>Some providers process data in countries outside your own. Calry uses the safeguards required by applicable law, such as adequacy decisions or approved contractual clauses, where relevant.</p>
</section>
<section>
  <h2>6. Retention and deletion</h2>
  <p>Account, profile, meal and progress data are generally kept while your account is active so Calry can provide history and personalized estimates. Subscription and transaction records may be kept for the period required for accounting, fraud prevention and legal compliance. Administrative security audit records are pseudonymized and normally retained for no more than {{AUDIT_RETENTION_DAYS}} days. Identifiers held only to complete a retryable deletion are erased when that deletion completes. Provider backups and other records may remain for a limited period where necessary and are deleted or de-identified when no longer required.</p>
  <p>You may request account and data deletion through the deletion control in Calry when available, or by contacting {{CONTACT}}. Deleting the app from your device does not by itself delete the account. Calry may ask you to verify ownership before acting on a request.</p>
</section>
<section>
  <h2>7. Your choices and rights</h2>
  <p>Depending on your location, you may have rights to access, correct, export, delete, restrict or object to processing, withdraw consent and complain to your local data-protection authority. You can edit many profile and meal details directly in Calry. For any other request, contact {{CONTACT}}.</p>
  <p>You can manage or cancel subscriptions in the Apple App Store or Google Play account used to purchase them. Authentication permissions can also be reviewed in your Apple or Google account.</p>
</section>
<section>
  <h2>8. Security</h2>
  <p>Calry uses access controls, authenticated API requests, encrypted network connections and service-provider security controls designed to protect personal data. No method of storage or transmission is completely secure, so absolute security cannot be guaranteed.</p>
</section>
<section>
  <h2>9. Children</h2>
  <p>Calry is not directed to children who cannot lawfully consent to the processing of their data in their country. A parent or guardian who believes a child provided data without the required authorization should contact {{CONTACT}}.</p>
</section>
<section>
  <h2>10. Changes and contact</h2>
  <p>Calry may update this policy when the product, providers or legal requirements change. The effective date at the top will be revised, and material changes may also be communicated in the app.</p>
  <p>Privacy questions and requests can be sent to {{CONTACT}}. You may also lodge a complaint with the data-protection authority where you live.</p>
</section>
""",
    },
    ("privacy", "it"): {
        "title": "Informativa privacy",
        "description": "Come Calry raccoglie, usa e protegge i dati personali.",
        "lead": "Una spiegazione chiara dei dati necessari a Calry per stimare i pasti, mantenere il bilancio giornaliero e gestire gli abbonamenti.",
        "footer": "Consapevolezza, senza sensi di colpa.",
        "body": """
<section>
  <h2>1. Titolare del trattamento</h2>
  <p><strong>{{OPERATOR_NAME}}</strong> gestisce l’app mobile Calry ed è titolare del trattamento dei dati personali descritti in questa informativa. Per domande o richieste privacy è possibile contattare {{CONTACT}}.</p>
  <p>L’informativa si applica all’app Calry, ai relativi servizi backend e a queste pagine legali pubbliche. I servizi di terze parti collegati da Calry hanno informative proprie.</p>
</section>
<section>
  <h2>2. Dati raccolti</h2>
  <h3>Account e identità</h3>
  <ul>
    <li>Identificativo Firebase, indirizzo email, nome visualizzato e provider Google o Apple scelto per l’accesso.</li>
    <li>Identificativo interno dell’account e stato dell’onboarding.</li>
  </ul>
  <h3>Profilo e dati sul benessere</h3>
  <ul>
    <li>Obiettivo calorico, tipo di obiettivo, età, altezza, peso, formula selezionata, livello di attività, unità preferite e altre impostazioni fornite.</li>
    <li>Riepiloghi giornalieri, calorie assunte e bruciate, registrazioni dell’acqua, categorie dei pasti e cronologia dei progressi.</li>
  </ul>
  <h3>Contenuti relativi a pasti e attività</h3>
  <ul>
    <li>Descrizioni, foto e registrazioni vocali inviate; trascrizioni; alimenti rilevati; stime di calorie e macronutrienti; correzioni e conferme.</li>
    <li>Attività e calorie bruciate inserite manualmente.</li>
    <li>Memorie alimentari ricavate dalle conferme, per riconoscere con maggiore coerenza i pasti ripetuti.</li>
  </ul>
  <h3>Abbonamenti, dati tecnici e assistenza</h3>
  <ul>
    <li>Dati su entitlement, prodotto, store, rinnovo e scadenza ricevuti da RevenueCat. Calry non riceve i dati completi della carta di pagamento.</li>
    <li>Indirizzo IP, metadati delle richieste, versione dell’app, informazioni su dispositivo e diagnostica, tracce di prestazione e segnalazioni di errore generate durante l’uso.</li>
    <li>Messaggi e informazioni inviati in caso di assistenza o esercizio di un diritto privacy.</li>
  </ul>
</section>
<section>
  <h2>3. Finalità e basi giuridiche</h2>
  <ul>
    <li><strong>Fornire il servizio:</strong> autenticare l’utente, stimare i pasti, calcolare il bilancio, sincronizzare la cronologia, salvare preferenze e offrire le funzioni premium.</li>
    <li><strong>Personalizzare e migliorare:</strong> applicare le correzioni, rendere più coerenti le stime ricorrenti, risolvere malfunzionamenti e comprendere la qualità del servizio.</li>
    <li><strong>Gestire gli acquisti:</strong> validare e ripristinare gli abbonamenti, gestire gli entitlement e prevenire abusi di fatturazione.</li>
    <li><strong>Proteggere Calry:</strong> tutelare account e infrastruttura, indagare abusi e applicare i termini.</li>
    <li><strong>Adempiere a obblighi di legge:</strong> conservare registrazioni o rispondere a richieste legittime quando necessario.</li>
  </ul>
  <p>A seconda del luogo in cui si trova l’utente, le basi giuridiche sono l’esecuzione del contratto richiesto, il consenso, il legittimo interesse a gestire e proteggere Calry e l’adempimento di obblighi legali. Quando profilo o pasti costituiscono dati relativi alla salute, Calry si basa sul consenso esplicito o su un’altra base consentita dalla legge applicabile. Il consenso può essere revocato per il futuro, ma alcune funzioni non saranno più disponibili.</p>
  <p class="note"><strong>Nessuna vendita o pubblicità di terze parti.</strong> Calry non vende dati personali e non usa i dati di pasti o profilo per pubblicità mirata di terze parti.</p>
</section>
<section>
  <h2>4. Trattamento tramite AI</h2>
  <p>Calry usa sistemi di intelligenza artificiale per analizzare testo, foto o audio che l’utente sceglie di inviare. Il contenuto pertinente e le istruzioni tecniche vengono trasmessi a OpenRouter e al provider del modello selezionato tramite tale servizio (attualmente Google Gemini) per produrre una stima o una trascrizione. È opportuno non includere dati di altre persone o informazioni sensibili non necessarie alla stima.</p>
  <p>I risultati AI possono essere inesatti. Calry può conservare input, output, identificativi dei modelli, latenza e diagnostica degli errori per fornire la funzione, verificarne la qualità e mantenere una traccia di audit. L’accesso è limitato alle necessità operative.</p>
  <p>Le stime AI non producono decisioni con effetti giuridici o analogamente significativi sull’utente.</p>
</section>
<section>
  <h2>5. Destinatari dei dati</h2>
  <p>Calry condivide solo i dati ragionevolmente necessari affinché i seguenti fornitori svolgano i propri servizi:</p>
  <ul>
    <li><strong>Google Firebase</strong> per autenticazione e verifica dei token.</li>
    <li><strong>Apple e Google</strong> per i rispettivi servizi di accesso e per gli acquisti in-app effettuati tramite gli store.</li>
    <li><strong>RevenueCat</strong> per stato degli abbonamenti, entitlement ed eventi di acquisto.</li>
    <li><strong>Railway e infrastruttura PostgreSQL</strong> per API, database ed elaborazioni in background.</li>
    <li><strong>Il provider di archiviazione media configurato</strong> per foto dei pasti e registrazioni vocali.</li>
    <li><strong>OpenRouter e i provider AI instradati</strong> per elaborare testo, foto e audio dei pasti.</li>
    <li><strong>Sentry</strong>, quando attivo, per monitoraggio di errori, prestazioni e diagnostica.</li>
    <li>Consulenti professionali, autorità o un eventuale operatore subentrante quando richiesto dalla legge, per tutelare diritti o nell’ambito di una legittima operazione societaria.</li>
  </ul>
  <p>Alcuni fornitori possono trattare dati in Paesi diversi da quello dell’utente. Quando necessario, Calry applica le garanzie richieste dalla legge, come decisioni di adeguatezza o clausole contrattuali approvate.</p>
</section>
<section>
  <h2>6. Conservazione e cancellazione</h2>
  <p>I dati di account, profilo, pasti e progressi sono generalmente conservati mentre l’account è attivo, per offrire cronologia e stime personalizzate. I dati relativi ad abbonamenti e transazioni possono essere conservati per i periodi necessari a contabilità, prevenzione frodi e obblighi legali. I registri amministrativi di sicurezza sono pseudonimizzati e normalmente conservati per non più di {{AUDIT_RETENTION_DAYS}} giorni. Gli identificativi necessari soltanto a completare una cancellazione ripetibile vengono eliminati al suo completamento. Backup dei fornitori e altri registri possono permanere per un periodo limitato quando necessario e vengono eliminati o de-identificati quando non più richiesti.</p>
  <p>È possibile richiedere la cancellazione dell’account e dei dati tramite il comando disponibile in Calry, quando presente, oppure contattando {{CONTACT}}. Disinstallare l’app non cancella automaticamente l’account. Prima di procedere Calry può chiedere di verificarne la titolarità.</p>
</section>
<section>
  <h2>7. Scelte e diritti</h2>
  <p>A seconda del luogo in cui si trova, l’utente può avere diritto ad accesso, rettifica, portabilità, cancellazione, limitazione, opposizione, revoca del consenso e reclamo all’autorità di controllo competente. Molti dati di profilo e pasto possono essere modificati direttamente in Calry. Per altre richieste è possibile contattare {{CONTACT}}.</p>
  <p>Gli abbonamenti possono essere gestiti o annullati nell’account App Store o Google Play usato per l’acquisto. Le autorizzazioni di accesso possono essere riviste anche nell’account Apple o Google.</p>
</section>
<section>
  <h2>8. Sicurezza</h2>
  <p>Calry usa controlli di accesso, richieste API autenticate, connessioni di rete cifrate e misure di sicurezza dei fornitori per proteggere i dati personali. Nessun sistema di archiviazione o trasmissione è completamente sicuro e non è quindi possibile garantire una sicurezza assoluta.</p>
</section>
<section>
  <h2>9. Minori</h2>
  <p>Calry non è destinata a minori che, nel proprio Paese, non possono prestare validamente il consenso al trattamento. Un genitore o tutore che ritenga siano stati forniti dati senza l’autorizzazione necessaria può contattare {{CONTACT}}.</p>
</section>
<section>
  <h2>10. Modifiche e contatti</h2>
  <p>Calry può aggiornare l’informativa quando cambiano prodotto, fornitori o requisiti legali. La data in alto verrà aggiornata e le modifiche sostanziali potranno essere comunicate anche nell’app.</p>
  <p>Domande e richieste privacy possono essere inviate a {{CONTACT}}. È inoltre possibile presentare reclamo all’autorità di protezione dati competente.</p>
</section>
""",
    },
    ("terms", "en"): {
        "title": "Terms & Conditions",
        "description": "The terms governing access to and use of Calry.",
        "lead": "The rules that keep Calry useful, safe and transparent—including how AI estimates and subscriptions work.",
        "footer": "Made for calm, everyday awareness.",
        "body": """
<section>
  <h2>1. Agreement and provider</h2>
  <p>These Terms &amp; Conditions form an agreement between you and <strong>{{OPERATOR_NAME}}</strong>, the operator of Calry. By creating an account, accessing the app or purchasing Calry Pro, you agree to these terms and the Privacy Policy. If you do not agree, do not use Calry.</p>
  <p>You must have legal capacity to enter this agreement. If local law requires parental or guardian authorization, you may use Calry only with that authorization.</p>
</section>
<section>
  <h2>2. What Calry provides</h2>
  <p>Calry is a calorie-awareness tool that lets you record meals using text, photos or voice, estimate calories and macronutrients with AI, record activity and water, and review daily or historical balances. Features may differ by platform, country, app version and subscription tier.</p>
  <p>Calry may change, improve, suspend or discontinue features. Where a change materially affects a paid service, Calry will provide notice or remedies required by applicable law.</p>
</section>
<section>
  <h2>3. Estimates—not medical advice</h2>
  <p>Food recognition, calorie targets, macronutrients, activity values, insights and other outputs are estimates. They may be incomplete or inaccurate, particularly when portions, ingredients or preparation methods are unclear.</p>
  <p>Calry is not a medical device and does not provide medical, dietary or clinical advice. It is not intended to diagnose, treat or prevent any condition and must not be used for emergencies. Consult a qualified professional before making decisions where health risks, pregnancy, an eating disorder, allergies, medication or a medical condition may be involved.</p>
  <p>You remain responsible for reviewing estimates and deciding whether they are appropriate for you.</p>
</section>
<section>
  <h2>4. Accounts</h2>
  <p>You sign in through supported identity providers such as Apple or Google. You are responsible for keeping that account secure, providing accurate information and notifying Calry of suspected unauthorized access. You may not impersonate others, share access unlawfully or create accounts to evade restrictions.</p>
</section>
<section>
  <h2>5. Calry Pro subscriptions</h2>
  <ul>
    <li>The price, billing period, trial or introductory offer, included features and renewal terms are shown on the purchase screen before you confirm.</li>
    <li>Subscriptions are processed by Apple App Store or Google Play and managed through RevenueCat. Payment is charged to the store account you use.</li>
    <li>Unless the purchase screen states otherwise, subscriptions renew automatically until cancelled. Cancel through your App Store or Google Play subscription settings before the renewal deadline shown by the store.</li>
    <li>Deleting Calry or your Calry account does not automatically cancel a store subscription.</li>
    <li>Refunds, billing disputes, trial conversion and cancellation timing are governed by the applicable store rules and mandatory consumer law.</li>
  </ul>
  <p>Calry may change future pricing or plan contents with the notice and consent required by the store and applicable law. Previously purchased rights and mandatory consumer protections remain unaffected.</p>
</section>
<section>
  <h2>6. Your content</h2>
  <p>You retain ownership of meal descriptions, photos, audio, corrections and other content you submit. You grant Calry a limited, worldwide, non-exclusive licence to host, reproduce, transform and process that content only as needed to provide, secure and improve the service, including through the providers identified in the Privacy Policy.</p>
  <p>You confirm that you have the right to submit the content and that it does not unlawfully reveal another person’s private information or infringe intellectual-property or other rights.</p>
</section>
<section>
  <h2>7. Acceptable use</h2>
  <p>You may not:</p>
  <ul>
    <li>use Calry for unlawful, harmful, fraudulent or abusive activity;</li>
    <li>upload malware or content that violates another person’s rights;</li>
    <li>attempt to bypass authentication, subscription controls, rate limits or security measures;</li>
    <li>scrape, reverse engineer or interfere with Calry except where applicable law expressly permits it;</li>
    <li>use automated systems in a way that degrades the service or creates unreasonable cost.</li>
  </ul>
</section>
<section>
  <h2>8. Calry property and third-party services</h2>
  <p>The Calry name, brand, interface, software and original content are owned by {{OPERATOR_NAME}} or its licensors. These terms grant only a personal, limited, revocable, non-transferable right to use the app.</p>
  <p>Calry depends on third-party services including identity providers, app stores, RevenueCat, hosting, storage and AI providers. Their separate terms may apply, and temporary failures or changes outside Calry’s reasonable control can affect availability.</p>
</section>
<section>
  <h2>9. Availability, disclaimers and liability</h2>
  <p>Calry is provided with reasonable care and skill, but continuous or error-free operation is not guaranteed. To the maximum extent permitted by law, Calry is provided “as is” and “as available,” without warranties beyond those that cannot legally be excluded.</p>
  <p>To the maximum extent permitted by law, {{OPERATOR_NAME}} is not liable for indirect or consequential losses, loss of data or decisions made in reliance on an AI estimate. Nothing in these terms excludes liability that cannot lawfully be limited, or any mandatory rights you have as a consumer.</p>
</section>
<section>
  <h2>10. Suspension, termination and deletion</h2>
  <p>You may stop using Calry at any time and request deletion as explained in the Privacy Policy. Suspension or termination is separate from account deletion. Calry may proportionately restrict access where reasonably necessary to protect users or infrastructure, comply with law, address fraud or respond to a serious or repeated breach of these terms. Depending on urgency, restrictions may be immediate. Where required or reasonably possible, Calry will provide the reason, duration, subscription consequences and a way to request review through {{CONTACT}}.</p>
  <p>Removing promotional access does not cancel an Apple App Store or Google Play subscription. If access to a paid service is restricted, billing, cancellation and any refund remain subject to the applicable store rules and mandatory consumer law.</p>
  <p>Terms that by their nature should survive termination—including ownership, disclaimers and accrued payment obligations—continue to apply.</p>
</section>
<section>
  <h2>11. Governing law and changes</h2>
  <p>These terms are governed by Italian law, without depriving consumers of mandatory protections or the courts available to them under the law of their country of residence.</p>
  <p>Calry may update these terms for legal, security or product reasons. The effective date will change and material updates may be communicated in the app. Continued use after an update takes effect constitutes acceptance where permitted by law; otherwise Calry will request consent.</p>
</section>
<section>
  <h2>12. Contact</h2>
  <p>Questions about these terms can be sent to {{CONTACT}}.</p>
</section>
""",
    },
    ("terms", "it"): {
        "title": "Termini e condizioni",
        "description": "I termini che regolano l’accesso e l’utilizzo di Calry.",
        "lead": "Le regole che rendono Calry utile, sicura e trasparente, incluso il funzionamento delle stime AI e degli abbonamenti.",
        "footer": "Pensata per una consapevolezza quotidiana e serena.",
        "body": """
<section>
  <h2>1. Accordo e fornitore</h2>
  <p>I presenti Termini e condizioni costituiscono un accordo tra l’utente e <strong>{{OPERATOR_NAME}}</strong>, gestore di Calry. Creando un account, accedendo all’app o acquistando Calry Pro, l’utente accetta questi termini e l’Informativa privacy. Se non li accetta, non deve usare Calry.</p>
  <p>L’utente deve avere la capacità giuridica necessaria per concludere il presente accordo. Se la legge locale richiede l’autorizzazione di un genitore o tutore, Calry può essere usata solo con tale autorizzazione.</p>
</section>
<section>
  <h2>2. Il servizio Calry</h2>
  <p>Calry è uno strumento di consapevolezza calorica che consente di registrare pasti tramite testo, foto o voce, stimare calorie e macronutrienti con l’AI, aggiungere attività e acqua e consultare bilanci giornalieri o storici. Le funzioni possono variare in base a piattaforma, Paese, versione e piano di abbonamento.</p>
  <p>Calry può modificare, migliorare, sospendere o interrompere funzioni. Se una modifica incide in modo sostanziale su un servizio a pagamento, verranno forniti il preavviso o i rimedi richiesti dalla legge applicabile.</p>
</section>
<section>
  <h2>3. Stime, non consulenza medica</h2>
  <p>Riconoscimento degli alimenti, obiettivi calorici, macronutrienti, valori delle attività, insight e altri risultati sono stime. Possono essere incompleti o inesatti, soprattutto quando porzioni, ingredienti o preparazione non sono chiari.</p>
  <p>Calry non è un dispositivo medico e non fornisce consulenza medica, dietetica o clinica. Non è destinata a diagnosticare, trattare o prevenire patologie e non deve essere usata in emergenza. È necessario consultare un professionista qualificato prima di decisioni che coinvolgano rischi per la salute, gravidanza, disturbi alimentari, allergie, farmaci o condizioni mediche.</p>
  <p>L’utente resta responsabile di verificare le stime e decidere se siano adeguate alle proprie esigenze.</p>
</section>
<section>
  <h2>4. Account</h2>
  <p>L’accesso avviene tramite provider supportati, come Apple o Google. L’utente è responsabile della sicurezza del relativo account, dell’accuratezza delle informazioni fornite e della segnalazione di accessi sospetti. Non è consentito impersonare altri, condividere illecitamente l’accesso o creare account per eludere restrizioni.</p>
</section>
<section>
  <h2>5. Abbonamenti Calry Pro</h2>
  <ul>
    <li>Prezzo, periodo di fatturazione, eventuale prova o offerta introduttiva, funzioni incluse e condizioni di rinnovo sono mostrati nella schermata di acquisto prima della conferma.</li>
    <li>Gli abbonamenti sono elaborati da Apple App Store o Google Play e gestiti tramite RevenueCat. L’importo viene addebitato all’account dello store usato per l’acquisto.</li>
    <li>Salvo diversa indicazione nella schermata di acquisto, l’abbonamento si rinnova automaticamente fino alla cancellazione. La cancellazione va effettuata nelle impostazioni abbonamenti di App Store o Google Play entro il termine indicato dallo store.</li>
    <li>Disinstallare Calry o cancellare l’account Calry non annulla automaticamente l’abbonamento nello store.</li>
    <li>Rimborsi, contestazioni, conversione delle prove e tempistiche di annullamento seguono le regole dello store e la normativa inderogabile a tutela dei consumatori.</li>
  </ul>
  <p>Calry può modificare prezzi futuri o contenuti dei piani con il preavviso e il consenso richiesti dallo store e dalla legge. Restano salvi i diritti già acquistati e le tutele inderogabili del consumatore.</p>
</section>
<section>
  <h2>6. Contenuti dell’utente</h2>
  <p>L’utente mantiene la titolarità di descrizioni, foto, audio, correzioni e altri contenuti inviati. Concede a Calry una licenza limitata, mondiale, non esclusiva per ospitare, riprodurre, trasformare e trattare tali contenuti solo quanto necessario a fornire, proteggere e migliorare il servizio, anche tramite i fornitori indicati nell’Informativa privacy.</p>
  <p>L’utente conferma di avere il diritto di inviare i contenuti e che questi non rivelino illecitamente informazioni private di terzi né violino proprietà intellettuale o altri diritti.</p>
</section>
<section>
  <h2>7. Uso consentito</h2>
  <p>Non è consentito:</p>
  <ul>
    <li>usare Calry per attività illegali, dannose, fraudolente o abusive;</li>
    <li>caricare malware o contenuti che violino diritti altrui;</li>
    <li>tentare di aggirare autenticazione, controlli dell’abbonamento, limiti o misure di sicurezza;</li>
    <li>estrarre dati, effettuare reverse engineering o interferire con Calry, salvo quanto espressamente consentito dalla legge;</li>
    <li>usare sistemi automatizzati in modo da degradare il servizio o creare costi irragionevoli.</li>
  </ul>
</section>
<section>
  <h2>8. Proprietà di Calry e servizi terzi</h2>
  <p>Nome, marchio, interfaccia, software e contenuti originali di Calry appartengono a {{OPERATOR_NAME}} o ai suoi licenzianti. I presenti termini concedono esclusivamente un diritto personale, limitato, revocabile e non trasferibile di usare l’app.</p>
  <p>Calry dipende da servizi terzi, tra cui provider di identità, store, RevenueCat, hosting, archiviazione e AI. Possono applicarsi i loro termini e guasti o modifiche temporanee fuori dal ragionevole controllo di Calry possono incidere sulla disponibilità.</p>
</section>
<section>
  <h2>9. Disponibilità, garanzie e responsabilità</h2>
  <p>Calry è fornita con ragionevole diligenza professionale, ma non è garantito un funzionamento continuo o privo di errori. Nei limiti consentiti dalla legge, Calry è fornita “così com’è” e “come disponibile”, senza garanzie ulteriori rispetto a quelle che non possono essere escluse.</p>
  <p>Nei limiti consentiti dalla legge, {{OPERATOR_NAME}} non risponde di danni indiretti o consequenziali, perdita di dati o decisioni assunte facendo affidamento su una stima AI. Nulla limita responsabilità che non possono essere escluse per legge o diritti inderogabili del consumatore.</p>
</section>
<section>
  <h2>10. Sospensione, recesso e cancellazione</h2>
  <p>L’utente può interrompere l’uso in qualsiasi momento e richiedere la cancellazione come indicato nell’Informativa privacy. La sospensione o cessazione dell’accesso è distinta dalla cancellazione dell’account. Calry può limitare proporzionalmente l’accesso quando ragionevolmente necessario per proteggere utenti o infrastruttura, adempiere alla legge, contrastare frodi o rispondere a violazioni gravi o ripetute. In base all’urgenza, la limitazione può essere immediata. Quando richiesto o ragionevolmente possibile, Calry comunica motivo, durata, conseguenze sull’abbonamento e modalità per chiedere riesame tramite {{CONTACT}}.</p>
  <p>La rimozione di un accesso promozionale non annulla un abbonamento Apple App Store o Google Play. Se viene limitato l’accesso a un servizio a pagamento, fatturazione, cancellazione ed eventuale rimborso restano soggetti alle regole dello store e alle norme imperative a tutela del consumatore.</p>
  <p>Le clausole che per loro natura devono sopravvivere, incluse titolarità, limitazioni e obblighi di pagamento maturati, continuano ad applicarsi.</p>
</section>
<section>
  <h2>11. Legge applicabile e modifiche</h2>
  <p>I presenti termini sono regolati dalla legge italiana, senza privare il consumatore delle tutele inderogabili o dei fori disponibili secondo la legge del proprio Paese di residenza.</p>
  <p>Calry può aggiornare i termini per ragioni legali, di sicurezza o di prodotto. La data di efficacia verrà aggiornata e le modifiche sostanziali potranno essere comunicate nell’app. L’uso successivo all’entrata in vigore costituisce accettazione quando consentito; negli altri casi verrà richiesto il consenso.</p>
</section>
<section>
  <h2>12. Contatti</h2>
  <p>Le domande relative ai presenti termini possono essere inviate a {{CONTACT}}.</p>
</section>
""",
    },
}
