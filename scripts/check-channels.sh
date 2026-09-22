#!/usr/bin/env bash
# Which of these channels can actually be crawled?
#
#   bash scripts/check-channels.sh @UPmusicTM Tavs_Club t.me/RadioJavan
#   bash scripts/check-channels.sh < channels.txt
#   bash scripts/check-channels.sh < channels.txt | grep '^ok' | cut -f2 > crawlable.txt
#
# The crawler reads https://t.me/s/<name>, the public web preview. A channel whose
# owner turned that off (or that Telegram restricts) redirects to the plain page and
# cannot be indexed at all — this asks the same question in one request per channel,
# before you add fifty of them and find out one by one.
#
# Output is tab-separated: status, username, note. Exit code 1 if nothing is usable.
set -uo pipefail

UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36'
DELAY="${DELAY:-1}"  # be as polite here as the crawler is

names=("$@")
if [ ${#names[@]} -eq 0 ]; then
  while read -r line; do
    [ -n "$line" ] && names+=("$line")
  done
fi
[ ${#names[@]} -gt 0 ] || { echo "usage: check-channels.sh @name ... | < channels.txt" >&2; exit 2; }

usable=0
for raw in "${names[@]}"; do
  name="${raw##*/}"          # t.me/foo and https://t.me/foo both give foo
  name="${name#@}"
  name="${name%%\?*}"
  [ -n "$name" ] || continue

  preview="$(curl -s -o /dev/null -w '%{http_code}' -A "$UA" "https://t.me/s/$name")"
  if [ "$preview" = "200" ]; then
    printf 'ok\t%s\tcrawlable\n' "$name"
    usable=$((usable + 1))
  else
    # Tell "no preview" apart from "no such channel": the contact page of a name
    # nobody owns still answers 200, titled "Telegram: Contact @name".
    page="$(curl -s -A "$UA" "https://t.me/$name")"
    if printf '%s' "$page" | grep -q 'tgme_page_extra'; then
      printf 'no\t%s\tpreview disabled by the channel\n' "$name"
    else
      printf 'no\t%s\tno such channel\n' "$name"
    fi
  fi
  sleep "$DELAY"
done

[ "$usable" -gt 0 ]
