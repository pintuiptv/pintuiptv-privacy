# pintuiptv-privacy

Pintu IPTV Privacy page and public Video Trends documents.

## Video Trends generator 1.2.1

The generator reads public Trakt endpoints without OAuth and publishes at most
100 real items per ranking. `trending`, `popular`, and weekly `watched` remain
official Trakt rankings; top rated, new releases, movies of the year, and shows
of the moment are documented Pintu composites.

New releases use a rolling window of four **calendar months** in UTC. The
calendar is queried in consecutive chunks of at most 31 days and results are
deduplicated before filtering. Movies require a reliable `released` date in
the inclusive window. Shows additionally require an absolute S01E01 premiere
and use only `show.first_aired`; event and episode dates are never substituted.
Future, missing-date, older, season-premiere, and ordinary-episode candidates
are excluded. `movies_of_the_year` intentionally retains its separate current
calendar-year rule.

ISO 8601 timestamps are normalized to timezone-aware UTC datetimes. Series
premieres, and movie releases carrying a time component, must not be later
than the exact UTC generation instant; a same-day future time is not accepted.
Movie values containing only `YYYY-MM-DD` remain calendar dates.

Generation and semantic validation complete before the output directory is
atomically replaced, preserving the previous valid files when a run fails.
Five staggered daily workflow schedules refresh the documents and report
source, window, candidate, exclusion, and publication metrics.
