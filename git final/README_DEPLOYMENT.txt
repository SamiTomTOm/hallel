HALLEL INVENTORY — RECONCILED SOURCE PACKAGE

Included files are named for the project layout:
  main.py, models.py, database.py, waitlist.py, telegram_bot.py, telegram_admin.py, index.html

What was reconciled:
- Kept the newer index_.html profile dropdown/profile editor together with the notification and existing dashboard UI.
- Moved the profile edit modal markup before the application script. In the uploaded source it was after the script, so profile event handlers tried to bind before the modal existed.
- Kept the uploaded backend/model features: sale voiding, commission, notifications, profile endpoints and additive profile_image_url migration.
- Updated the Cloudinary signature endpoint to accept either CLOUDINARY_URL or the separate CLOUDINARY_CLOUD_NAME / CLOUDINARY_API_KEY / CLOUDINARY_API_SECRET variables.
- Profile editing only changes username and picture; it does not accept or change a user's role.

Deployment safety:
1. Back up the current GitHub project and Vercel deployment first.
2. Do not upload inventory.db to Vercel or overwrite Neon data. The local database file was intentionally excluded.
3. Do not include .env or secrets in this ZIP.
4. Replace the matching source files in your repository, then deploy.
5. Test login, dashboard, notifications, profile editing and a sale void before treating the deployment as verified.

Validation performed: Python syntax compilation and frontend JavaScript syntax check passed. No live Vercel, Neon or Cloudinary integration test was performed.
