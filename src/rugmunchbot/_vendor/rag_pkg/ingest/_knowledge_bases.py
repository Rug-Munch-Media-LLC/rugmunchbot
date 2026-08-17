# VENDORED 2026-08-17 from rmi-backend/app/rag/ingest/_knowledge_bases.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Curated knowledge base data for the RAG ingest pipeline.

Extracted verbatim from ``app.rag.ingest_crypto_knowledge`` during the
de-godding split (2026-08-14). Pure data — no imports beyond typing, no
I/O. The collection map is consumed by ``ingest_all`` (in ``_orchestrator``)
and re-exported by the ``ingest_crypto_knowledge`` shim.
"""

from __future__ import annotations

from typing import Any

SCAM_INTEL: list[dict[str, Any]] = [
    {
        "doc_id": "scam:honeypot",
        "content": (
            "HONEYPOT SCAM: A token that allows buys but blocks sells. The contract contains a "
            "blacklist/whitelist mechanism where only specific addresses can sell. Key indicators: "
            "(1) Trading is enabled but transfers revert for non-whitelisted addresses when selling. "
            "(2) The contract uses OpenZeppelin's Ownable pattern with custom transfer restrictions. "
            "(3) Check if _beforeTokenTransfer or _transfer functions contain address checks that "
            "prevent certain addresses from transferring. (4) Use simulated sells to detect if the "
            "transaction would revert. (5) High buy pressure with zero sell volume is a red flag. "
            "Detection method: Deploy a simulation contract that attempts to buy then immediately "
            "sell. If the sell reverts with a custom error, it is a honeypot. Severity: CRITICAL."
        ),
        "metadata": {"severity": "critical", "category": "honeypot", "chain": "all"},
    },
    {
        "doc_id": "scam:rugpull",
        "content": (
            "RUG PULL DETECTION: A rug pull occurs when developers drain liquidity or mint/sell their "
            "tokens, crashing the price. Key patterns: (1) Liquidity pool has no lock or very short "
            "lock duration (<24h). (2) Large percentage of tokens held in a single wallet (deployer). "
            "(3) Mint function is not revoked — anyone can create unlimited tokens. (4) Ownership is "
            "not renounced and the owner can call functions that drain the pool. (5) High tax on "
            "transfers (buy/sell tax >10%) that funnels to deployer wallet. (6) Suspicious token "
            "distribution: large allocation to team/insider wallets without vesting. (7) Check for "
            "hidden mint functions or proxy patterns that allow contract upgrades. (8) Recent deployer "
            "activity: wallet has launched multiple tokens that all rugged. Severity: CRITICAL."
        ),
        "metadata": {"severity": "critical", "category": "rugpull", "chain": "all"},
    },
    {
        "doc_id": "scam:flash_loan",
        "content": (
            "FLASH LOAN ATTACK: Exploits that use uncollateralized flash loans to manipulate markets. "
            "Attack pattern: (1) Attacker takes a flash loan of a large amount of tokens. (2) Uses "
            "borrowed tokens to manipulate price oracle or DEX pool. (3) Exploits the manipulated "
            "price to drain another protocol. (4) Repays the flash loan within the same transaction. "
            "Key vulnerabilities: (1) Protocols using spot price from a single DEX as oracle. (2) "
            "Lack of slippage protection on swaps. (3) Token pairs with low liquidity that can be "
            "easily manipulated. (4) Protocols that don't validate oracle price against multiple "
            "sources. Mitigation: Use TWAP (Time-Weighted Average Price) oracles, Chainlink price "
            "feeds, or multi-source price validation. Severity: HIGH."
        ),
        "metadata": {"severity": "high", "category": "flash_loan", "chain": "all"},
    },
    {
        "doc_id": "scam:oracle_manipulation",
        "content": (
            "ORACLE MANIPULATION: Attackers manipulate the price feed that a protocol relies on. "
            "Types: (1) Spot price manipulation — attacker swaps tokens to move the DEX spot price, "
            "then exploits protocols using that price. (2) Oracle frontrunning — attacker sees a "
            "pending oracle update and frontruns it. (3) Stale oracle — protocol uses outdated "
            "price data. (4) Single-source oracle — relying on one DEX pair for price. Detection: "
            "Check if protocol uses TWAP or multiple oracle sources. Verify oracle update frequency. "
            "Test if a large swap significantly moves the reported price. Severity: HIGH."
        ),
        "metadata": {"severity": "high", "category": "oracle", "chain": "all"},
    },
    {
        "doc_id": "scam:sandwich_attack",
        "content": (
            "SANDWICH ATTACK: MEV attack where attacker places orders before and after a victim's "
            "transaction. Attack flow: (1) Attacker sees pending swap transaction in mempool. "
            "(2) Attacker submits buy order with higher gas before victim (frontrun). (3) Victim's "
            "swap executes at worse price due to slippage. (4) Attacker sells immediately after "
            "(backrun) for profit. Detection: Check for transactions in the same block where tokens "
            "are bought and sold around a target swap. Look for addresses consistently performing "
            "this pattern. Impact: Variable, typically 0.5-3% of trade value per attack. Mitigation: "
            "Use slippage protection, private mempools, or MEV-resistant DEX designs."
        ),
        "metadata": {"severity": "medium", "category": "mev", "chain": "all"},
    },
    {
        "doc_id": "scam:proxy_risk",
        "content": (
            "PROXY CONTRACT RISK: Upgradeable contracts that can be modified by the owner at any "
            "time. Red flags: (1) The implementation contract can be upgraded without timelock. "
            "(2) Owner is an EOA (Externally Owned Account), not a multisig. (3) No transparent "
            "proxy pattern used (UUPS proxies hide the implementation address). (4) The proxy "
            "admin can drain funds or rug through an upgrade. (5) Implementation is not verified "
            "on block explorer. Detection: Check for ERC-1967 proxy storage slots. Verify "
            "implementation contract source code. Check if there is a timelock on upgrades. "
            "Look for TransparentUpgradeableProxy or UUPS patterns. Severity: HIGH."
        ),
        "metadata": {"severity": "high", "category": "proxy", "chain": "evm"},
    },
    {
        "doc_id": "scam:fake_token",
        "content": (
            "FAKE TOKEN: A token impersonating a legitimate project to trick buyers. Indicators: "
            "(1) Token name/symbol matches a known project but contract address is different. "
            "(2) No liquidity or extremely low liquidity. (3) Contract is a direct copy of a "
            "legitimate token with no changes except deployer parameters. (4) Metadata (name, "
            "symbol, decimals) can be changed by owner. (5) Social media/website is fake or "
            "recently created. Detection: Verify contract address against official sources "
            "(CoinGecko, CoinMarketCap, project website). Check deployer history for similar "
            "tokens. Compare bytecode with known projects. Severity: MEDIUM."
        ),
        "metadata": {"severity": "medium", "category": "impersonation", "chain": "all"},
    },
    {
        "doc_id": "scam:dusting_attack",
        "content": (
            "DUSTING ATTACK: Attackers send tiny amounts of tokens (dust) to many wallets to "
            "track and de-anonymize them. Purpose: (1) Link wallet addresses to real identities "
            "by tracking when dust is moved or consolidated. (2) Phishing attacks — the dust "
            "transaction contains a memo with a malicious link. Prevention: (1) Don't interact "
            "with unknown tokens received. (2) Use privacy tools to consolidate funds. (3) "
            "Ignore dust amounts in wallet UI. Detection: Monitor for small unsolicited token "
            "transfers (<$0.01 value) from unknown addresses. Severity: LOW."
        ),
        "metadata": {"severity": "low", "category": "privacy", "chain": "all"},
    },
    {
        "doc_id": "scam:governance_attack",
        "content": (
            "GOVERNANCE ATTACK: On-chain governance subverted to move funds or change protocol "
            "parameters. Mechanisms: (1) Flash-loan governance attack — attacker borrows enough "
            "voting tokens for one block, passes a malicious proposal, then repays the loan in "
            "the same transaction (Beanstalk Farms, April 2022, ~$182M). (2) Vote buying / "
            "bribery in veTokenomics gauge votes. (3) Quorum manipulation with low-turnout "
            "votes. (4) Proposal spam burying a malicious transfer. (5) Timelock bypass when "
            "upgrades are executable immediately. Detection: check proposal quorum vs "
            "flash-loanable supply, timelock duration, and who holds vote weight. Mitigation: "
            "timelocks, quorum floors, vote-weight vesting, emergency pause. Severity: CRITICAL."
        ),
        "metadata": {"severity": "critical", "category": "governance", "chain": "evm"},
    },
    {
        "doc_id": "scam:read_only_reentrancy",
        "content": (
            "READ-ONLY REENTRANCY: A view/pure function is re-entered mid-transaction and "
            "returns stale state to another protocol, which then acts on the wrong data. The "
            "vulnerable contract appears safe because only read functions are involved, but the "
            "attacker mutates state in a separate call and re-enters the read path to get "
            "inconsistent balances. Exploits: Platypus Finance (Feb 2023, ~$8.5M) and Curve "
            "Vyper aftermath (July 2023). Detection: trace view functions that read balances "
            "during external callbacks; check lending/vault integrations that price from "
            "balanceOf of a contract that calls back. Mitigation: snapshot pricing, "
            "reentrancy guards on read paths that feed economic decisions. Severity: HIGH."
        ),
        "metadata": {"severity": "high", "category": "reentrancy", "chain": "evm"},
    },
    {
        "doc_id": "scam:permit_phishing",
        "content": (
            "PERMIT SIGNATURE PHISHING: EIP-2612 permit lets a token holder authorize spending "
            "with a signed message instead of an approve() transaction. Phishing sites request "
            "a harmless-looking signature ('sign to claim', 'sign to verify wallet') and then "
            "submit the permit themselves, draining tokens gaslessly. Because the victim never "
            "sends a transfer transaction, the theft looks like it came from nowhere. "
            "Detection: watch for permit signatures collected by malicious dApps; educate users "
            "that signing typed data can move funds. Mitigation: only sign typed data on "
            "verified sites, use hardware wallets that display the signing payload, revoke "
            "stale approvals. Severity: HIGH."
        ),
        "metadata": {"severity": "high", "category": "phishing", "chain": "evm"},
    },
    {
        "doc_id": "scam:fake_nft_mint",
        "content": (
            "FAKE NFT MINT / COLLECTION: Scam mints impersonate a popular NFT project or "
            "celebrity drop, collecting mint fees or approvals and delivering nothing. "
            "Mechanics: (1) Fake collection with a lookalike name/symbol and a different "
            "contract address. (2) Mint site that drains wallet approvals instead of minting. "
            "(3) Pre-minted supply dumped on secondary markets. Detection: verify collection "
            "address against official sources, check the mint contract for withdraw functions, "
            "check deployer history for repeated fake mints. Severity: HIGH."
        ),
        "metadata": {"severity": "high", "category": "impersonation", "chain": "evm"},
    },
    {
        "doc_id": "scam:jit_liquidity_rug",
        "content": (
            "JIT LIQUIDITY / CONCENTRATED-LIQUIDITY RUG: On Uniswap v3-style AMMs, a rug is "
            "not limited to removing LP — the deployer can (1) add concentrated liquidity in a "
            "narrow range, collect fees, then remove it (JIT liquidity tax on every swap), or "
            "(2) create a pool with one-sided liquidity so buys fill at inflated prices. "
            "Detection: check tick-range positions and their owner, verify liquidity is "
            "distributed across ranges, and watch for positions opened/closed in the same "
            "block around large swaps. Severity: HIGH."
        ),
        "metadata": {"severity": "high", "category": "liquidity", "chain": "evm"},
    },
]

DEPLOYER_HISTORY: list[dict[str, Any]] = [
    {
        "doc_id": "history:luna_collapse",
        "content": (
            "LUNA/UST COLLAPSE (May 2022): Terra's algorithmic stablecoin UST lost its $1 peg, "
            "triggering a death spiral that wiped out ~$60B in value. Mechanism: (1) UST was "
            "backed by LUNA through an arbitrage mechanism — 1 UST could always be swapped for "
            "$1 worth of LUNA. (2) Large UST sell pressure caused the peg to break. (3) The "
            "arbitrage mechanism minted more LUNA to absorb UST sells, hyperinflating LUNA supply "
            "from ~350M to ~6.5T tokens. (4) Both LUNA and UST became essentially worthless. "
            "Key lessons: Algorithmic stablecoins without external collateral are vulnerable to "
            "death spirals. Always check the collateralization ratio and redemption mechanisms. "
            "Do Kwon was later arrested for fraud. Severity: HISTORICAL-CRITICAL."
        ),
        "metadata": {"severity": "critical", "incident": "luna", "date": "2022-05"},
    },
    {
        "doc_id": "history:ftx_collapse",
        "content": (
            "FTX COLLAPSE (November 2022): Centralized exchange FTX filed for bankruptcy after "
            "a bank run revealed an $8B hole in customer funds. Mechanism: (1) FTX had been "
            "loaning customer deposits to its sister trading firm Alameda Research. (2) Alameda "
            "used FTX's native FTT token as collateral. (3) When FTT price dropped after a "
            "CoinDesk report revealed Alameda's balance sheet was mostly FTT, the collateral "
            "became worthless. (4) Customers rushed to withdraw, revealing FTX was insolvent. "
            "Key lessons: (1) Never trust centralized exchanges with all your funds — use "
            "self-custody. (2) Exchange tokens used as collateral create circular risk. "
            "(3) Proof-of-reserves without proof-of-liabilities is meaningless. (4) Sam "
            "Bankman-Fried was convicted of fraud. Severity: HISTORICAL-CRITICAL."
        ),
        "metadata": {"severity": "critical", "incident": "ftx", "date": "2022-11"},
    },
    {
        "doc_id": "history:squid_game_token",
        "content": (
            "SQUID GAME TOKEN RUG PULL (November 2021): A token named after the Netflix show "
            "Squid Game rose from $0.01 to $2,861 before developers drained ~$3.3M. Mechanism: "
            "(1) Token had an anti-dumping mechanism that prevented selling. (2) Only the "
            "developers could sell. (3) After the price peak, developers withdrew all liquidity "
            "and disappeared. (4) The project's website and social media were deleted. Key "
            "indicators: (1) No sell functionality except for whitelisted addresses. (2) "
            "Anonymous team. (3) Unrealistic marketing promises. (4) Website registered "
            "anonymously. Severity: HISTORICAL-CRITICAL."
        ),
        "metadata": {"severity": "critical", "incident": "squid_game", "date": "2021-11"},
    },
    {
        "doc_id": "history:wormhole_hack",
        "content": (
            "WORMHOLE BRIDGE HACK (February 2022): The Wormhole cross-chain bridge was exploited "
            "for ~$326M (120K wETH). Mechanism: (1) Attacker exploited a signature verification "
            "vulnerability in the bridge's Solana program. (2) They minted 120K wETH on Solana "
            "without depositing equivalent ETH on Ethereum. (3) The attacker converted 93,750 "
            "wETH to ETH and bridged the rest. (4) Jump Crypto (parent company) replenished the "
            "funds to prevent ecosystem collapse. Key lessons: (1) Bridge contracts are high-value "
            "targets. (2) Cross-chain message verification must be foolproof. (3) Always check "
            "bridge audit reports and exploit history. Severity: HISTORICAL-CRITICAL."
        ),
        "metadata": {"severity": "critical", "incident": "wormhole", "date": "2022-02"},
    },
    {
        "doc_id": "history:ronin_bridge_hack",
        "content": (
            "RONIN BRIDGE HACK (March 2022): Axie Infinity's Ronin bridge was exploited for "
            "~$620M (173,600 ETH + 25.5M USDC). Mechanism: (1) Attacker gained control of 5 "
            "of 9 validator nodes (4 Sky Mavis validators + 1 Axie DAO validator). (2) Used "
            "compromised keys to approve two withdrawal transactions. (3) Funds were laundered "
            "through Tornado Cash and CEXs. (4) Lazarus Group (North Korea) was identified as "
            "the attacker. Key lessons: (1) Validator decentralization is critical — 5/9 "
            "multisig is centralized. (2) Always verify validator set distribution. (3) Bridge "
            "security requires defense-in-depth. Severity: HISTORICAL-CRITICAL."
        ),
        "metadata": {"severity": "critical", "incident": "ronin", "date": "2022-03"},
    },
    {
        "doc_id": "history:three_arrows_capital",
        "content": (
            "3AC (THREE ARROWS CAPITAL) COLLAPSE (June-July 2022): Major crypto hedge fund "
            "defaulted on $3.5B+ in loans after excessive leverage on LUNA/UST and GBTC trades. "
            "Mechanism: (1) 3AC was heavily leveraged on the LUNA ecosystem (lost ~$560M when "
            "LUNA collapsed). (2) Also had large GBTC (Grayscale Bitcoin Trust) position betting "
            "on NAV premium convergence that never happened. (3) When prices dropped, lenders "
            "issued margin calls that 3AC couldn't meet. (4) 3AC filed for bankruptcy, triggering "
            "contagion across crypto lending (Voyager, BlockFi, Genesis). Key lessons: (1) "
            "Counterparty risk in centralized lending. (2) Excessive leverage magnifies losses. "
            "(3) Concentration risk in correlated assets. Severity: HISTORICAL-HIGH."
        ),
        "metadata": {"severity": "high", "incident": "3ac", "date": "2022-06"},
    },
    {
        "doc_id": "history:multichain_exploit",
        "content": (
            "MULTICHAIN BRIDGE EXPLOIT (July 2023): ~$126M was drained from Multichain's "
            "Fantom and Moonriver bridges. Mechanism: (1) The CEO was arrested in China and the "
            "MPC keys were seized by authorities. (2) Funds were moved to unknown addresses. "
            "Key lessons: (1) Centralized key management is a single point of failure. (2) "
            "Founder/keyholder risk is real for bridges. (3) MPC is not a substitute for "
            "decentralization. Severity: HISTORICAL-CRITICAL."
        ),
        "metadata": {"severity": "critical", "incident": "multichain", "date": "2023-07"},
    },
    {
        "doc_id": "history:beanstalk_governance",
        "content": (
            "BEANSTALK FLASH-LOAN GOVERNANCE ATTACK (April 2022): Beanstalk, an algorithmic "
            "stablecoin protocol, was drained of ~$182M via the first major flash-loan "
            "governance attack. Mechanism: (1) Attacker took a flash loan of Beans and ETH. "
            "(2) Used the borrowed capital to acquire a majority of governance voting weight "
            "(Stalk) in one block. (3) Passed a malicious proposal that transferred protocol "
            "funds to the attacker. (4) Repaid the flash loan in the same transaction. Key "
            "lessons: (1) Governance quorum must exceed what a flash loan can buy. (2) "
            "Timelocks give the community a window to respond. (3) Vote weight based on "
            "instantaneously borrowable supply is manipulable. Severity: HISTORICAL-CRITICAL."
        ),
        "metadata": {"severity": "critical", "incident": "beanstalk", "date": "2022-04"},
    },
    {
        "doc_id": "history:euler_finance",
        "content": (
            "EULER FINANCE EXPLOIT (March 2023): Lending protocol Euler was drained of ~$197M "
            "in one transaction. Mechanism: (1) Attacker used flash loans to inflate a "
            "collateral asset's value in the protocol by donating tokens directly (donation "
            "without share minting), a donation-based accounting flaw. (2) Borrowed against "
            "the inflated collateral value. (3) Withdrew the borrowed assets, leaving the "
            "protocol undercollateralized. Key lessons: (1) Balance-donation inflation is an "
            "accounting bug class that also affects ERC-4626-style vaults. (2) Flash loans "
            "amplify small accounting flaws. (3) Funds were largely returned after negotiation "
            "with whitehats and the attacker. Severity: HISTORICAL-CRITICAL."
        ),
        "metadata": {"severity": "critical", "incident": "euler", "date": "2023-03"},
    },
    {
        "doc_id": "history:nomad_bridge",
        "content": (
            "NOMAD BRIDGE EXPLOIT (August 2022): Cross-chain bridge Nomad was drained of "
            "~$190M via a mass copy-paste exploit. Mechanism: (1) A 'trusted root' optimization "
            "was introduced without initializing the trusted root — it was left at zero. (2) "
            "The zero root meant the message verification check passed trivially for arbitrary "
            "messages. (3) The first exploiter's transaction was publicly copyable; hundreds of "
            "addresses replayed it with their own recipients, draining the bridge. Key "
            "lessons: (1) Uninitialized critical state is a bridge-killing bug. (2) Public "
            "exploits can be mass-replayed by copycats. (3) Verify bridge initialization "
            "state and message validation thoroughly. Severity: HISTORICAL-CRITICAL."
        ),
        "metadata": {"severity": "critical", "incident": "nomad", "date": "2022-08"},
    },
    {
        "doc_id": "history:mango_markets",
        "content": (
            "MANGO MARKETS ORACLE MANIPULATION (October 2022): Solana perp DEX Mango Markets "
            "was drained of ~$114M. Mechanism: (1) Attacker deposited ~$5M USDC. (2) Used a "
            "large MNGO-PERP position to move the MNGO oracle price up ~1300%. (3) Borrowed "
            "against the inflated collateral value using the protocol's own perp as the "
            "manipulation tool — no flash loan needed. Key lessons: (1) Perp markets whose "
            "oracle is derived from their own order book/pool can be self-manipulated. (2) "
            "Collateral value derived from manipulable spot/perp prices is dangerous. (3) "
            "Liquidation thresholds must account for oracle manipulability. Severity: "
            "HISTORICAL-CRITICAL."
        ),
        "metadata": {"severity": "critical", "incident": "mango", "date": "2022-10"},
    },
    {
        "doc_id": "history:curve_vyper",
        "content": (
            "CURVE VYPER COMPILER BUG (July 2023): A Vyper compiler bug (versions 0.2.15-0.2.16) "
            "broke the @nonreentrant decorator — the reentrancy lock flag was not properly set, "
            "so guarded functions were re-entrant. Curve pools deployed with the buggy compiler "
            "(alETH/ETH, pETH/ETH, msETH/ETH, CRV/ETH) were drained for over $60M total. "
            "Key lessons: (1) Compiler-level bugs can bypass reentrancy guards in production "
            "contracts. (2) Verify the exact compiler version + settings behind deployed "
            "bytecode. (3) Read-only reentrancy variants appeared in the aftermath, showing "
            "the attack surface extends beyond write functions. Severity: HISTORICAL-CRITICAL."
        ),
        "metadata": {"severity": "critical", "incident": "curve_vyper", "date": "2023-07"},
    },
    {
        "doc_id": "history:platypus_reentrancy",
        "content": (
            "PLATYPUS FINANCE READ-ONLY REENTRANCY (February 2023): Solana-side Avalanche "
            "stablecoin AMM Platypus was drained of ~$8.5M. Mechanism: (1) Attacker used a "
            "flash loan in one transaction. (2) Re-entered a read-only balance function of "
            "the pool mid-callback and minted LP tokens against stale balances. (3) Withdrew "
            "the inflated LP for profit. Key lessons: (1) Read-only reentrancy: view functions "
            "that feed pricing can be exploited even when write functions are guarded. (2) "
            "Protocols that price deposits from balanceOf of another contract must snapshot. "
            "(3) Flash loans turn small accounting inconsistencies into large drains. "
            "Severity: HISTORICAL-HIGH."
        ),
        "metadata": {"severity": "high", "incident": "platypus", "date": "2023-02"},
    },
    {
        "doc_id": "history:kyberswap_elastic",
        "content": (
            "KYBERSWAP ELASTIC EXPLOIT (November 2023): KyberSwap Elastic AMM pools were "
            "drained of ~$48M. Mechanism: (1) Exploited a tick-crossing precision issue in "
            "the concentrated-liquidity swap loop. (2) A crafted swap sequence manipulated "
            "the math across tick boundaries to extract tokens beyond what the pool should "
            "pay. (3) Multiple pools were drained in one transaction. Key lessons: (1) "
            "Concentrated-liquidity tick math is precision-sensitive and must be "
            "invariant-tested. (2) Complex swap loops are a rich audit surface. (3) "
            "Cross-pool composability can amplify a single bug. Severity: HISTORICAL-CRITICAL."
        ),
        "metadata": {"severity": "critical", "incident": "kyberswap", "date": "2023-11"},
    },
]

CONTRACT_AUDIT: list[dict[str, Any]] = [
    {
        "doc_id": "audit:token_analysis_methodology",
        "content": (
            "TOKEN ANALYSIS METHODOLOGY: A systematic approach to evaluating token safety. "
            "Phase 1 - Basic Checks: (1) Verify contract is verified on block explorer. "
            "(2) Check token name, symbol, decimals are standard. (3) Identify if token "
            "is a proxy and check upgradeability. (4) Verify mint function is revoked or "
            "properly restricted. (5) Check if ownership is renounced. Phase 2 - Liquidity "
            "Analysis: (1) Check DEX liquidity amount and distribution. (2) Verify LP tokens "
            "are burned or locked. (3) Check lock duration on platforms like Unicrypt, "
            "Team Finance. (4) Analyze buy/sell tax rates. Phase 3 - Holder Analysis: "
            "(1) Check top 10 holder concentration. (2) Identify if deployer still holds "
            "large amounts. (3) Look for bundler/sniper bot wallets in early buys. "
            "(4) Check for wash trading patterns. Phase 4 - Social/Community: (1) Verify "
            "social media presence and engagement. (2) Check for paid engagement/bots. "
            "(3) Verify team is doxxed or anonymous. (4) Review audit reports if available."
        ),
        "metadata": {"category": "methodology", "severity": "informational"},
    },
    {
        "doc_id": "audit:liquidity_pool_safety",
        "content": (
            "LIQUIDITY POOL SAFETY ANALYSIS: Key checks for DEX liquidity pools. (1) LP "
            "token status: Are LP tokens burned (sent to dead address) or locked in a "
            "time-lock contract? Burned LP tokens are ideal — they cannot be removed. "
            "Locked LP tokens expire after the lock period — check remaining duration. "
            "(2) Pool concentration: How many LP providers? Single provider = high risk. "
            "(3) Pool size relative to market cap: Low liquidity relative to FDV means "
            "high price impact on trades. (4) LP withdrawal history: Has the pool been "
            "recently drained or refilled? (5) Fee structure: Unusual fee tiers may "
            "indicate manipulation. (6) For Uniswap V3: Check concentrated liquidity "
            "ranges — large positions at current price can exit quickly."
        ),
        "metadata": {"category": "liquidity", "severity": "high"},
    },
    {
        "doc_id": "audit:tokenomics_check",
        "content": (
            "TOKENOMICS ANALYSIS: Evaluate token economic design for red flags. (1) Supply: "
            "Fixed or inflationary? Check if total supply can be increased. (2) Distribution: "
            "How are tokens allocated? Typical allocation: Team 10-20%, Investors 10-20%, "
            "Community/Ecosystem 30-50%, Liquidity 10-20%. Red flag: Team + insiders >50%. "
            "(3) Vesting: Are team/investor tokens vested? Check for linear vesting over "
            "1-4 years with cliff period. Red flag: No vesting or tokens unlock in <30 days. "
            "(4) Emission rate: For inflationary tokens, what is the annual inflation rate? "
            ">20% annual inflation is a red flag. (5) Burn mechanism: Is there a deflationary "
            "mechanism (buyback and burn, fee burn)? (6) Utility: What is the token actually "
            "used for? Governance tokens with no revenue share have limited value capture."
        ),
        "metadata": {"category": "tokenomics", "severity": "high"},
    },
    {
        "doc_id": "audit:holder_analysis",
        "content": (
            "HOLDER ANALYSIS TECHNIQUE: Analyzing wallet distributions to detect manipulation. "
            "(1) Top 10 concentration: If top 10 holders own >80% of supply, high centralization "
            "risk. (2) Deployer holdings: Check if the deployer wallet retains tokens. Large "
            "deployer holdings without timelock = rug risk. (3) Bundler detection: Look for "
            "wallets that bought in the same block as launch with identical amounts — these "
            "are likely bundled/sniper buys. Bundler presence suggests organized launch. "
            "(4) Wash trading: Look for wallets that repeatedly buy and sell the same amounts, "
            "creating fake volume. (5) Connected wallets: Use on-chain clustering to identify "
            "wallets funded from the same source (same funder = same entity). (6) Smart money: "
            "Are known successful traders/investors holding or avoiding the token?"
        ),
        "metadata": {"category": "holders", "severity": "medium"},
    },
    {
        "doc_id": "audit:tax_analysis",
        "content": (
            "TRANSFER TAX ANALYSIS: Understanding buy/sell taxes and their implications. "
            "(1) Tax structure: What are the buy and sell taxes? Typical: 0-5%. Red flag: "
            "Sell tax >10% or buy tax >5%. (2) Tax destination: Where do collected taxes go? "
            "Marketing wallet? LP? Burn address? Red flag: Taxes go to deployer wallet. "
            "(3) Tax mutability: Can tax rates be changed? Check for setTax or similar "
            "functions. (4) Tax exemption: Are there tax-exempt addresses? The deployer "
            "being tax-exempt is a red flag. (5) Max transaction amount: Is there a max "
            "tx limit that prevents whales from selling? This protects against dumps but "
            "also prevents legitimate exits."
        ),
        "metadata": {"category": "tax", "severity": "medium"},
    },
    {
        "doc_id": "audit:reentrancy_pattern",
        "content": (
            "REENTRANCY AUDIT PATTERN: The classic EVM exploit (2016 DAO hack, ~$60M). The "
            "vulnerable shape makes an external call BEFORE updating state, so a malicious "
            "recipient can re-enter the function while balances are stale:\n"
            "```solidity\n"
            "function withdraw(uint256 amount) external {\n"
            "    require(balances[msg.sender] >= amount);\n"
            '    (bool ok, ) = msg.sender.call{value: amount}(""); // interaction FIRST\n'
            "    balances[msg.sender] -= amount;                     // effect LAST\n"
            "}\n"
            "```\n"
            "Variants: cross-function reentrancy (re-entering a different function with a "
            "shared flag), ERC-777/ERC-1155 callback reentrancy via tokensReceived/on"
            "ERC1155Received, and read-only reentrancy (view functions feeding stale "
            "balances to other protocols). Mitigations: checks-effects-interactions, "
            "nonReentrant guards, pull-over-push payments. Audit: flag every external call "
            "before a state update and every custom fallback/recipient hook. Severity: "
            "CRITICAL."
        ),
        "metadata": {"category": "vulnerability_pattern", "severity": "critical", "pattern": "reentrancy"},
    },
    {
        "doc_id": "audit:integer_overflow_pattern",
        "content": (
            "INTEGER OVERFLOW/UNDERFLOW AUDIT PATTERN: Pre-0.8 Solidity silently wrapped on "
            "arithmetic overflow, letting balances become astronomically large or "
            "underflow to near-max:\n"
            "```solidity\n"
            "// Solidity < 0.8: wraps silently\n"
            "function transfer(address to, uint256 amount) external {\n"
            "    balances[msg.sender] -= amount; // underflow if amount > balance\n"
            "    balances[to] += amount;         // overflow wraps\n"
            "}\n"
            "```\n"
            "Solidity 0.8+ reverts by default, but unchecked blocks, unsafe casts, and "
            "non-EVM chains still wrap. Audit: check arithmetic on balances, allowances, "
            "and token amounts; look for unchecked{} blocks, casts, and custom VMs. "
            "Severity: HIGH."
        ),
        "metadata": {"category": "vulnerability_pattern", "severity": "high", "pattern": "integer_overflow"},
    },
    {
        "doc_id": "audit:tx_origin_pattern",
        "content": (
            "TX.ORIGIN AUTHORIZATION AUDIT PATTERN: tx.origin returns the account that "
            "STARTED the transaction, not the immediate caller, so a phishing contract "
            "can pass an owner check:\n"
            "```solidity\n"
            "function withdrawAll() external {\n"
            "    require(tx.origin == owner); // attacker's contract passes this\n"
            "    payable(owner).transfer(address(this).balance);\n"
            "}\n"
            "```\n"
            "Victim calls attacker's contract; attacker's contract calls withdrawAll(); "
            "tx.origin is the victim, so the check passes. Use msg.sender for "
            "authorization. tx.origin is only acceptable for explicit anti-phishing "
            "conditions. Audit: grep for tx.origin in require/if conditions. Severity: "
            "HIGH."
        ),
        "metadata": {"category": "vulnerability_pattern", "severity": "high", "pattern": "tx_origin"},
    },
    {
        "doc_id": "audit:unchecked_external_call_pattern",
        "content": (
            "UNCHECKED EXTERNAL CALL AUDIT PATTERN: Low-level calls return a success "
            "boolean instead of reverting; ignoring it lets a contract continue as if the "
            "call succeeded:\n"
            "```solidity\n"
            '(bool success, ) = recipient.call{value: amount}("");\n'
            "// success ignored — accounting proceeds as if the payment landed\n"
            "```\n"
            "This corrupts balances and lets attackers complete flows that should have "
            "failed. Also related: transfer()/send() with fixed 2300 gas can brick "
            "payments as gas costs rise. Audit: every external interaction must handle "
            "failure explicitly; prefer high-level interface calls that revert. Severity: "
            "HIGH."
        ),
        "metadata": {"category": "vulnerability_pattern", "severity": "high", "pattern": "unchecked_call"},
    },
    {
        "doc_id": "audit:delegatecall_pattern",
        "content": (
            "DELEGATECALL AUDIT PATTERN: delegatecall executes the callee's code in the "
            "CALLER's storage context, so pointing it at an untrusted address lets that "
            "address rewrite any storage variable — owner, balances, everything. The 2016 "
            "Parity wallet hack destroyed ~$150M of ETH this way:\n"
            "```solidity\n"
            "function execute(address target, bytes calldata data) external {\n"
            "    target.delegatecall(data); // runs in THIS contract's storage\n"
            "}\n"
            "```\n"
            "Audit: guard the target address, keep proxy storage collision-free, and "
            "never let users supply arbitrary delegatecall targets. Severity: CRITICAL."
        ),
        "metadata": {"category": "vulnerability_pattern", "severity": "critical", "pattern": "delegatecall"},
    },
    {
        "doc_id": "audit:access_control_pattern",
        "content": (
            "ACCESS CONTROL AUDIT PATTERN: Privileged functions callable by anyone because "
            "the owner/admin modifier is missing. Ranks first in OWASP's smart-contract "
            "top 10 and caused the largest dollar losses of any vulnerability class in "
            "2024-2025 incident data:\n"
            "```solidity\n"
            "function mint(address to, uint256 amount) external { // no onlyOwner\n"
            "    _mint(to, amount); // anyone can mint unlimited supply\n"
            "}\n"
            "```\n"
            "Audit: enumerate every state-changing function, verify a role check exists, "
            "and test that unprivileged callers revert. Also check proxy admins, "
            "upgrade paths, and roles that can bypass a renounced owner. Severity: "
            "CRITICAL."
        ),
        "metadata": {"category": "vulnerability_pattern", "severity": "critical", "pattern": "access_control"},
    },
]

PHISHING_DB: list[dict[str, Any]] = [
    {
        "doc_id": "defi:amm_basics",
        "content": (
            "AUTOMATED MARKET MAKER (AMM): A decentralized exchange mechanism that uses "
            "liquidity pools instead of order books. Key concepts: (1) Constant Product "
            "Formula (x * y = k): Used by Uniswap V2, where x and y are token reserves "
            "and k is constant. Price is determined by the ratio of reserves. (2) "
            "Concentrated Liquidity (Uniswap V3): LPs provide liquidity within specific "
            "price ranges for better capital efficiency. (3) Impermanent Loss: The "
            "temporary loss LPs experience when token prices diverge from the deposit "
            "ratio. The greater the divergence, the larger the loss. (4) Slippage: The "
            "difference between expected and executed price due to trade size relative "
            "to pool liquidity. (5) Price Impact: How much a trade moves the pool price. "
            "Large trades in low-liquidity pools have high price impact. Common AMMs: "
            "Uniswap, SushiSwap, PancakeSwap, Curve (stablecoin-focused), Balancer."
        ),
        "metadata": {"category": "defi", "topic": "amm", "severity": "informational"},
    },
    {
        "doc_id": "defi:lending_protocols",
        "content": (
            "LENDING PROTOCOLS (DeFi): Platforms that allow users to lend and borrow crypto "
            "assets without intermediaries. Key concepts: (1) Collateralization Ratio: "
            "The ratio of collateral value to borrowed amount. Over-collateralized loans "
            "require >100% collateral (typically 150-200%). (2) Liquidation: When collateral "
            "value drops below the required ratio, the position is liquidated — collateral "
            "is sold to repay the loan, often with a penalty. (3) Health Factor: A measure "
            "of position safety. Health factor < 1 means liquidation is possible. (4) APY "
            "vs APR: APY includes compound interest. Supply APY is what lenders earn; "
            "borrow APY is what borrowers pay. (5) Flash Loans: Uncollateralized loans "
            "that must be borrowed and repaid in the same transaction. Used for arbitrage, "
            "liquidation, and (maliciously) for exploits. Major protocols: Aave, Compound, "
            "MakerDAO (now Sky), Venus, Morpho."
        ),
        "metadata": {"category": "defi", "topic": "lending", "severity": "informational"},
    },
    {
        "doc_id": "defi:yield_farming",
        "content": (
            "YIELD FARMING / LIQUIDITY MINING: Providing liquidity to DeFi protocols to "
            "earn rewards, typically in the protocol's native token. Key concepts: "
            "(1) APR/APY: Annual Percentage Rate/Yield — often extremely high initially "
            "to attract liquidity, then declines. (2) Reward tokens: The native token "
            "earned as rewards. If reward token price drops, real yield drops. (3) "
            "Impermanent Loss risk: LP positions can lose value compared to just holding "
            "the tokens. (4) Smart contract risk: Funds are locked in smart contracts "
            "that may have bugs or be exploited. (5) Rug pull risk: Protocol developers "
            "can drain funds if contracts are upgradeable or contain backdoors. (6) "
            "Compounding: Reinvesting rewards to earn yield on yield. Red flags: "
            "Unsustainably high APY (>1000%), anonymous team, no audit, no timelock "
            "on contract changes, reward token has no utility beyond farming."
        ),
        "metadata": {"category": "defi", "topic": "yield", "severity": "informational"},
    },
    {
        "doc_id": "defi:bridge_security",
        "content": (
            "CROSS-CHAIN BRIDGE SECURITY: Bridges enable token transfers between blockchains "
            "but are frequent exploit targets. Security models: (1) Lock-and-Mint: Original "
            "tokens are locked on source chain, wrapped tokens minted on destination. "
            "(2) Burn-and-Mint: Tokens are burned on source and minted on destination. "
            "(3) Liquidity Pool: Pre-funded pools of tokens on each chain. Key risks: "
            "(1) Validator compromise: If bridge validators are compromised, attacker can "
            "mint unbacked tokens. (2) Smart contract bugs: Verification/validation logic "
            "errors. (3) Centralization: Many bridges have centralized validator sets "
            "(e.g., 5-15 validators). (4) Upgradeability: Bridge contracts that can be "
            "upgraded without timelock. Red flags when evaluating: Bridge has been "
            "exploited before, small validator set, anonymous validators, unaudited code. "
            "Historical losses: Ronin $620M, Wormhole $326M, Multichain $126M, Nomad $190M."
        ),
        "metadata": {"category": "defi", "topic": "bridge", "severity": "high"},
    },
    {
        "doc_id": "defi:stablecoins",
        "content": (
            "STABLECOINS: Tokens designed to maintain a stable value, typically pegged to $1. "
            "Types: (1) Fiat-collateralized (USDC, USDT): Backed 1:1 by fiat reserves. "
            "Safest but centralized. (2) Crypto-collateralized (DAI, crvUSD): Backed by "
            "over-collateralized crypto positions. Decentralized but capital-inefficient. "
            "(3) Algorithmic (FRAX, formerly UST): Uses algorithms and incentives to "
            "maintain the peg without full collateral backing. Highest risk — UST death "
            "spiral showed fatal flaws. Key metrics: (1) Collateralization ratio: >100% "
            "means fully backed. (2) Peg stability: How often and how far from $1 does "
            "the price deviate? (3) Reserve composition: What backs the stablecoin? "
            "(4) Redemption mechanism: Can users redeem 1:1 for the underlying? "
            "(5) Depeg history: Has it ever lost significant value before? Red flags: "
            "Algorithmic with low collateral, opaque reserves, no redemption guarantee."
        ),
        "metadata": {"category": "defi", "topic": "stablecoin", "severity": "medium"},
    },
    {
        "doc_id": "defi:uniswap_v3_mechanics",
        "content": (
            "UNISWAP V3 / CONCENTRATED LIQUIDITY MECHANICS: LPs provide liquidity inside "
            "custom price ranges (ticks) instead of 0-to-infinity, earning higher fees per "
            "dollar when in range. Key mechanics: (1) Ticks: discrete price boundaries; "
            "positions are NFTs with custom ranges. (2) Tick ranges: liquidity is only "
            "active inside the range; when price exits, the position becomes single-sided. "
            "(3) TWAP oracles: v3 observes accumulated prices for TWAP, but short windows "
            "over thin ranges are manipulable. (4) JIT liquidity: bots add and remove "
            "concentrated liquidity in the same block as a large swap to capture fees. "
            "(5) Fee tiers: 0.05%, 0.3%, 1% routes change swap economics. Gotchas for "
            "token analysis: check liquidity distribution across ticks, not just TVL; "
            "check who holds active-range positions. Severity: informational."
        ),
        "metadata": {"category": "defi", "topic": "uniswap", "severity": "high"},
    },
    {
        "doc_id": "defi:curve_stableswap",
        "content": (
            "CURVE STABLE-SWAP POOL MECHANICS: Curve-style pools use a hybrid "
            "constant-sum/constant-product curve optimized for assets with similar value "
            "(stablecoins, liquid staking derivatives). Key mechanics: (1) Amplification "
            "coefficient (A) controls how flat the curve is near the peg; higher A = "
            "more efficient near peg, more vulnerable to imbalance. (2) Price stays near "
            "1:1 while pools are balanced; large imbalance moves price sharply. (3) "
            "Lending integrations (crvUSD, LLAMMA) price from Curve pools, so pool "
            "manipulation can cascade into liquidations. Gotchas: spot price from a thin "
            "Curve pool is flash-loan manipulable (Harvest Finance 2020); the 2023 "
            "Vyper bug hit Curve pools specifically. Check pool depth on both sides and "
            "whether TWAP or deviation guards protect any integration. Severity: high."
        ),
        "metadata": {"category": "defi", "topic": "curve", "severity": "high"},
    },
    {
        "doc_id": "defi:liquidation_auctions",
        "content": (
            "LIQUIDATION AUCTIONS AND CASCADES: Lending protocols close underwater "
            "positions by selling collateral, often at a discount via auction or a "
            "liquidation bonus. Mechanics: (1) Health factor < 1 triggers liquidation. "
            "(2) Liquidators race to close first and earn the bonus — MEV bots dominate. "
            "(3) Cascades: one liquidation drives price down, triggering more "
            "liquidations sharing the same price feed. (4) Bad debt: when collateral "
            "cannot cover the loan, the protocol eats the loss, hitting lenders. "
            "Gotchas: partial liquidation (Aave v3) dampens cascades; eMode "
            "correlated-asset baskets liquidate together; auction-style (Maker) has "
            "different dynamics than instant bonus liquidations. For token analysis, "
            "large borrowed positions against a volatile token are cascade fuel. "
            "Severity: high."
        ),
        "metadata": {"category": "defi", "topic": "liquidation", "severity": "high"},
    },
    {
        "doc_id": "defi:erc4626_vaults",
        "content": (
            "ERC-4626 VAULT MECHANICS: The tokenized-vault standard (yield vaults, "
            "staking wrappers) prices shares as assets-per-share: shares = deposit / "
            "pricePerShare. Key mechanics: (1) Donation-inflatable balance: a large "
            "donation before/after deposits inflates pricePerShare, hurting late or "
            "early depositors (first-depositor / share-inflation attack). (2) "
            "Rounding: deposit rounds shares down and withdrawal rounds assets down, "
            "which over many ops can drain a vault (extreme case: the 2021 Yearn "
            "v1 weirding / 'morphing' incidents). (3) fee accounting: fees accrue "
            "inside pricePerShare, so share price is not flat. Gotchas: verify "
            "minimum first deposit, virtual shares/offset (OpenZeppelin v5), and "
            "donation resistance before trusting a vault. Severity: high."
        ),
        "metadata": {"category": "defi", "topic": "vault", "severity": "high"},
    },
    {
        "doc_id": "defi:solana_program_mechanics",
        "content": (
            "SOLANA PROGRAM / ACCOUNT MODEL MECHANICS: Solana programs (smart contracts) "
            "receive all state as explicit accounts, so account validation IS the "
            "security model. Key mechanics: (1) Every account must be checked for "
            "program ownership, key match, and signer status — missing checks let "
            "attackers pass fake mints or token accounts. (2) CPIs call other programs; "
            "reentrancy-style issues come from state changed between CPI and later "
            "checks. (3) Token-2022 extensions (transfer fees, transfer hooks, "
            "confidential transfers) add programmable behavior on every transfer. "
            "(4) Priority fees + Jito bundles determine MEV ordering. (5) Rent and "
            "close-account mechanics differ from EVM storage. Gotchas: verify mint "
            "authority, freeze authority, and extension list before trusting a Solana "
            "token; simulate the full instruction set. Severity: high."
        ),
        "metadata": {"category": "defi", "topic": "solana", "severity": "high"},
    },
    {
        "doc_id": "defi:governance_mechanics",
        "content": (
            "ON-CHAIN GOVERNANCE MECHANICS: DAO governance moves protocol parameters and "
            "treasury funds through proposals. Key mechanics: (1) Quorum: minimum votes "
            "for a proposal to pass; quorum that a flash loan can buy is attackable. "
            "(2) Timelock: delay between proposal passing and execution — the community "
            "window to respond; no timelock means instant execution. (3) Vote weight: "
            "token holdings, veTokens, or delegated votes; instant-borrowable weight is "
            "manipulable. (4) Gauge/bribe mechanics in veTokenomics direct emissions. "
            "(5) Emergency pause/guardian roles can halt protocol activity. Gotchas: "
            "Beanstalk 2022 showed flash-loan governance; low-turnout proposals get "
            "buried; proposals bundling innocuous changes with a transfer get rubber-"
            "stamped. Severity: high."
        ),
        "metadata": {"category": "defi", "topic": "governance", "severity": "high"},
    },
]

WALLET_LABELS: list[dict[str, Any]] = [
    {
        "doc_id": "ta:support_resistance",
        "content": (
            "SUPPORT AND RESISTANCE LEVELS: Technical analysis concepts for identifying "
            "price levels where buying or selling pressure is expected. Support: A price "
            "level where buying interest is strong enough to prevent further decline. "
            "Resistance: A price level where selling pressure prevents further increases. "
            "Identification methods: (1) Historical price reversals: Levels where price "
            "reversed multiple times. (2) Round numbers: Psychological levels (e.g., $1, "
            "$10, $100 for tokens; $50K, $100K for BTC). (3) Moving averages: Commonly "
            "watched MAs act as dynamic support/resistance (20, 50, 200 period). "
            "(4) Volume profile: Price levels with highest trading volume (Volume "
            "Weighted Average Price - VWAP). (5) Fibonacci retracement levels: 38.2%, "
            "50%, 61.8% of prior trend. Patterns: Support becomes resistance when broken "
            "(and vice versa). Multiple touches of a level strengthen its significance."
        ),
        "metadata": {"category": "technical_analysis", "topic": "support_resistance"},
    },
    {
        "doc_id": "ta:indicators",
        "content": (
            "TECHNICAL INDICATORS for crypto trading. Trend indicators: (1) Moving Averages "
            "(SMA/EMA): Simple and exponential moving averages. Golden cross (50 crosses "
            "above 200) = bullish. Death cross (50 below 200) = bearish. (2) MACD: Moving "
            "Average Convergence Divergence — shows trend direction and momentum. Momentum "
            "indicators: (3) RSI (Relative Strength Index): 0-100 scale. >70 = overbought, "
            "<30 = oversold. Divergence between price and RSI signals reversal. (4) "
            "Stochastic Oscillator: Compares closing price to price range. Volume indicators: "
            "(5) OBV (On-Balance Volume): Cumulative volume that adds on up days, subtracts "
            "on down days. (6) Volume Profile: Shows traded volume at each price level. "
            "Volatility: (7) Bollinger Bands: Price envelope at 2 standard deviations from "
            "SMA. Squeeze (bands tighten) precedes breakout. (8) ATR (Average True Range): "
            "Measures volatility. Higher ATR = more volatile. On-chain specific: (9) NVT "
            "Ratio: Network Value to Transactions ratio — high NVT = overvalued. (10) MVRV: "
            "Market Value to Realized Value — >3.7 historically indicates cycle top."
        ),
        "metadata": {"category": "technical_analysis", "topic": "indicators"},
    },
    {
        "doc_id": "ta:chart_patterns",
        "content": (
            "CHART PATTERNS in crypto trading. Reversal patterns: (1) Head and Shoulders: "
            "Three peaks, middle highest. Break below neckline = bearish reversal. Inverse "
            "head and shoulders = bullish reversal. (2) Double Top/Bottom: Two peaks/troughs "
            "at similar levels. Break of the middle level confirms reversal. (3) Cup and "
            "Handle: U-shaped recovery followed by small consolidation — bullish continuation. "
            "Continuation patterns: (4) Bull/Bear Flags: Sharp move (pole) followed by "
            "consolidation in a channel (flag). Breakout in original direction. (5) Pennants: "
            "Similar to flags but with converging trendlines. (6) Ascending/Descending "
            "Triangles: Flat top/rising bottom or flat bottom/falling top — breakout in "
            "direction of the flat side. (7) Symmetrical Triangle: Converging trendlines — "
            "breakout can go either way. Candlestick patterns: (8) Doji: Open and close "
            "are nearly equal — indecision. (9) Hammer/Hanging Man: Small body with long "
            "lower wick — reversal signal. (10) Engulfing pattern: Second candle completely "
            "engulfs first — strong reversal signal."
        ),
        "metadata": {"category": "technical_analysis", "topic": "patterns"},
    },
    {
        "doc_id": "ta:market_structure",
        "content": (
            "MARKET STRUCTURE ANALYSIS for crypto. Trend identification: (1) Higher Highs + "
            "Higher Lows = Uptrend. Lower Highs + Lower Lows = Downtrend. Equal Highs + "
            "Equal Lows = Range/sideways. (2) Market phases: Accumulation (range after "
            "downtrend, smart money buying), Markup (uptrend, retail enters), Distribution "
            "(range after uptrend, smart money selling), Markdown (downtrend, retail panic). "
            "(3) Wyckoff Method: Schematic for identifying accumulation/distribution phases "
            "through price and volume analysis. Liquidity concepts: (4) Order blocks: Zones "
            "where large orders were previously executed — likely areas of support/resistance. "
            "(5) Fair Value Gaps (FVG): Price inefficiencies that tend to get filled. (6) "
            "Liquidity grabs: Fake breakouts that trap traders before reversing. Multi-timeframe "
            "analysis: (7) Higher timeframe = trend direction. (8) Intermediate timeframe = "
            "trade setup. (9) Lower timeframe = entry. Crypto-specific: (10) Exchange orderbook "
            "depth — large bid/ask walls indicate support/resistance. (11) Funding rates: "
            "Positive = longs pay shorts (bullish sentiment), negative = shorts pay longs "
            "(bearish sentiment). Extreme funding rates signal potential reversals."
        ),
        "metadata": {"category": "technical_analysis", "topic": "market_structure"},
    },
    {
        "doc_id": "ta:onchain_metrics",
        "content": (
            "ON-CHAIN METRICS for crypto analysis. Network health: (1) Active addresses: "
            "Daily/weekly/monthly unique addresses interacting with the chain. (2) Transaction "
            "count: Total and average transactions per day. (3) Hash rate: For PoW chains — "
            "higher = more secure. (4) Staking ratio: For PoS chains — percentage of supply "
            "staking. Valuation metrics: (5) NVT Ratio = Market Cap / Daily Transaction "
            "Volume. High NVT = network valued higher than its transaction utility suggests. "
            "(6) MVRV = Market Cap / Realized Cap. Realized cap values each coin at its "
            "last movement price. MVRV > 3.7 historically signals market top. (7) Puell "
            "Multiple = Daily miner revenue (USD) / 365-day MA of miner revenue. High value "
            "suggests miners may sell. (8) SOPR (Spent Output Profit Ratio) > 1 means coins "
            "are being sold at profit. Holder behavior: (9) HODL Waves: Distribution of "
            "coins by age since last moved. Older coins moving = potential sell pressure. "
            "(10) Exchange inflows/outflows: Large inflows to exchanges = potential selling. "
            "Large outflows = accumulation. (11) Whale activity: Transactions >$1M — "
            "tracking whale accumulation/distribution. (12) Supply in profit/loss: "
            "Extreme values signal market tops/bottoms."
        ),
        "metadata": {"category": "onchain", "topic": "metrics"},
    },
]

# Map collection names to knowledge bases
KNOWLEDGE_BASES: dict[str, list[dict[str, Any]]] = {
    "scam_intel": SCAM_INTEL,
    "deployer_history": DEPLOYER_HISTORY,
    "contract_audit": CONTRACT_AUDIT,
    "phishing_db": PHISHING_DB,
    "wallet_labels": WALLET_LABELS,
}
