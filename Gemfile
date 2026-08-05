source "https://rubygems.org"

gem "jekyll", "~> 4.3"
gem "jekyll-seo-tag", "~> 2.8"

# Ruby 3.0+ no longer starts a web server for `jekyll serve` without this.
gem "webrick", "~> 1.8"

# Ruby 3.4 and 4.0 moved these out of the default gems.
gem "csv"
gem "base64"
gem "bigdecimal"
gem "logger"
gem "ostruct"
gem "observer"

# Windows has no zoneinfo database, so `timezone:` in _config.yml cannot be
# resolved without these. Declared unconditionally: they are pure Ruby, weigh
# almost nothing, and keep the lockfile identical across platforms.
gem "tzinfo", ">= 2.0"
gem "tzinfo-data"

# Released eventmachine only, for `jekyll serve --livereload`. A yanked
# 1.3.0.dev prerelease circulated and will wedge a lockfile if resolved.
gem "eventmachine", "~> 1.2", ">= 1.2.7"
