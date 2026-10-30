<!--
own-voice lexicon.
Keep the "##" headings exactly as they are: the skills look them up by name.
Write everything else in the user's language (one block per language if they write in more than one).
No names, amounts, emails, phone numbers or keys. Run redact.py --check on this file before saving it.
When the spoken sheet and this lexicon disagree, this lexicon wins.
-->

# Lexicon

## Header

<!--
- Corpus: messages and words per register the counts come from, and the period.
- Languages covered.
- How the candidates were chosen: words and phrases typical of assistant-written text
  in this language (connectives, inflated verbs, courtesy formulas, openers, closers).
- Threshold used for the black list (for example: 0 to 2 occurrences in the whole corpus).
-->

## Black list

<!--
Terms the user (almost) never writes, each paired with what they actually write instead.
Counts come from lexicon.py count (theirs) and lexicon.py top (the substitute).

| Term | Their count per register | Their substitute (count) |
|---|---|---|
| <term> | friends 0, email 1 | <substitute> (123) |
-->

## Register differences

<!--
Terms that are theirs in one register and absent in another (for example used in email,
never with friends). State where each one is allowed.
-->

## Their words

<!--
Optional. Recurring words and phrases that are clearly theirs, with counts, grouped by use
(agreeing, thanking, connecting ideas, closing). Useful as substitutes and as positive models.
-->

## Check before copying

<!--
The plain list of every black-listed term, one per line, nothing else.
The same list is saved as <data>/voice/blacklist.txt for lexicon.py check.
-->
