-- One-off: carry an existing site's database from the old app name to the new one.
-- Run with the bench stopped (or in maintenance mode) and AFTER taking a backup.
-- Safe to run twice: the second run changes nothing.
-- The database client refuses updates without a key column unless this is off
SET SQL_SAFE_UPDATES = 0;
START TRANSACTION;

-- The app's module, and everything filed under it
UPDATE `tabModule Def` SET name = 'Murzak POS', module_name = 'Murzak POS', app_name = 'murzak_pos'
	WHERE app_name = 'techsavanna_pos';
UPDATE `tabDocType` SET module = 'Murzak POS' WHERE module = 'Techsavanna POS';
UPDATE `tabCustom Field` SET module = 'Murzak POS' WHERE module = 'Techsavanna POS';

-- Which apps this site has installed
UPDATE `tabInstalled Application` SET app_name = 'murzak_pos' WHERE app_name = 'techsavanna_pos';
UPDATE `tabDefaultValue` SET defvalue = REPLACE(defvalue, '"techsavanna_pos"', '"murzak_pos"')
	WHERE parent = '__global' AND defkey = 'installed_apps';

-- Patches already applied. Without this, the next migrate would run all of them again.
UPDATE `tabPatch Log` SET patch = REPLACE(patch, 'techsavanna_pos.', 'murzak_pos.')
	WHERE patch LIKE 'techsavanna\_pos.%';

-- Daily background jobs
UPDATE `tabScheduled Job Type` SET method = REPLACE(method, 'techsavanna_pos.', 'murzak_pos.')
	WHERE method LIKE 'techsavanna\_pos.%';

COMMIT;
