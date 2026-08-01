"""코레일 KTX 예매 텔레그램 봇."""

#: The single source of truth for the version. pyproject.toml reads it from
#: here rather than the other way round, so the running bot can name its own
#: version without depending on having been pip-installed - a source checkout
#: and a container built from one have to answer that question the same way.
#:
#: Bumping this is what makes the bot announce itself to its users on the next
#: start, so the entry in korail_bot.release_notes belongs in the same commit.
__version__ = "4.1.0"
