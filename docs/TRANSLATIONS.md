# Swahili (WS8.1)

The server's own messages (errors and confirmations) can be sent in Swahili. A person chooses their language with
`set_language("sw")`; the choice is kept on their user record, so every message after that comes in that language.
`get_languages()` lists the choices ("English" and "Kiswahili") and the current one. The calls are in
`murzak_pos/api/language_api.py`. Swahili is switched on in Frappe after every update.

## Where the words are

`murzak_pos/translations/sw.csv` has two columns: the English message exactly as written in the code, and the Swahili.
Frappe loads it by itself. Placeholders such as `{0}` and `{name}` must stay in the Swahili text, because the program
fills them in (a name, a number, an amount). Two automatic tests guard the file:

* every message in the code has a row, so a new message cannot ship without its Swahili;
* every row keeps the same placeholders as its English.

## Adding a message

1. Write the message in the code with `_("...")`.
2. Add a row to `sw.csv` with the English exactly as written and the Swahili beside it.
3. Run the unit tests.

## Needs a native speaker

The Swahili was drafted, not reviewed by a native speaker. Before it is shown to customers, have a Kenyan Swahili
speaker read `sw.csv` (open it in a spreadsheet; the second column is theirs to correct) and send it back. Points for the
reviewer: everyday shop words (till, receipt, invoice, stock, refund) should match what shop owners actually say; the
words "kaunta" (till), "risiti" (receipt), "ankara" (invoice) and "marejesho" (returns) are choices to confirm; and long
error messages may be shortened.

## Limits

* Only messages written with `_()` are translated. A few replies are built from other text and stay in English.
* The words on the screens belong to the frontend, which needs its own Swahili file and a language selector that calls
  `set_language`.
* The A4 print formats and receipts use the same translation file, so they print in the person's language.
