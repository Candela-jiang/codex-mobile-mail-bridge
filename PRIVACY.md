# Privacy Policy

Codex Mobile Mail Bridge is a local plugin for user-owned machines. It does not run a hosted service and does not send data to the plugin author.

When enabled, the bridge sends Codex work reports through the Gmail account configured by the user. Those emails may include model labels, project paths, Git status summaries, user instructions, and Codex responses. Email command replies from allowed senders are read from the configured Gmail inbox and passed to local Codex commands.

Gmail app passwords are stored locally by the setup script using Windows DPAPI for the current Windows user. Do not commit runtime files such as `config.json`, `gmail_app_password.dpapi`, logs, route maps, PID files, or processed-message caches.

Users are responsible for choosing recipients, allowed senders, and projects where sending work details over email is acceptable.
