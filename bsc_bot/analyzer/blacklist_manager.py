import aiosqlite
import logging
import os
import json
import hashlib
from datetime import datetime
from web3 import Web3

logger = logging.getLogger(__name__)

class BlacklistManager:
    def __init__(self, db_path="data/blacklist.db", fp_path="data/contract_fingerprints.json"):
        self.db_path = db_path
        self.fp_path = fp_path
        self._ensure_db_dir()
        self.fingerprints = self._load_fingerprints()

    def _ensure_db_dir(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        if not os.path.exists(self.fp_path):
             with open(self.fp_path, 'w') as f:
                 json.dump({}, f)

    def _load_fingerprints(self):
        try:
            if os.path.exists(self.fp_path):
                with open(self.fp_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load fingerprints: {e}")
        return {}

    def _save_fingerprints(self):
        try:
            with open(self.fp_path, 'w') as f:
                json.dump(self.fingerprints, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save fingerprints: {e}")

    def _extract_function_selectors(self, bytecode_hex):
        """Extract function selectors (PUSH4 instructions)"""
        if not bytecode_hex:
            return []
        
        # Remove 0x prefix
        if bytecode_hex.startswith('0x'):
            bytecode_hex = bytecode_hex[2:]
            
        selectors = []
        i = 0
        length = len(bytecode_hex)
        
        # PUSH4 opcode is 0x63
        while i < length:
            # Check for PUSH4 (0x63)
            # Since it's hex string, each byte is 2 chars. 0x63 is "63"
            if bytecode_hex[i:i+2] == "63":
                # Check if we have enough bytes left for 4-byte selector (8 hex chars)
                if i + 10 <= length:
                    selector = bytecode_hex[i+2:i+10]
                    selectors.append(selector)
                    i += 10 # Skip PUSH4 + 4 bytes
                    continue
            i += 2 # Next byte
            
        return sorted(selectors)

    def _calculate_fingerprint_hash(self, bytecode_hex):
        """Calculate SHA256 hash of sorted function selectors"""
        selectors = self._extract_function_selectors(bytecode_hex)
        if not selectors:
            return None
        
        # Join sorted selectors and hash
        fingerprint_str = "".join(selectors)
        return hashlib.sha256(fingerprint_str.encode()).hexdigest()

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS blacklist_code_hash (
                    hash TEXT PRIMARY KEY,
                    reason TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS blacklist_deployer (
                    address TEXT PRIMARY KEY,
                    reason TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS blacklist_code_fingerprint (
                    fingerprint TEXT PRIMARY KEY,
                    reason TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.commit()

    async def add_code_hash(self, code_hash, reason="Honeypot"):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO blacklist_code_hash (hash, reason) VALUES (?, ?)",
                (code_hash, reason)
            )
            await db.commit()
            logger.info(f"Added code hash to blacklist: {code_hash} ({reason})")

    async def check_code_hash(self, code_hash):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT reason FROM blacklist_code_hash WHERE hash = ?", (code_hash,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def add_deployer(self, address, reason="Rug Puller"):
        address = Web3.to_checksum_address(address)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO blacklist_deployer (address, reason) VALUES (?, ?)",
                (address, reason)
            )
            await db.commit()
            logger.info(f"Added deployer to blacklist: {address} ({reason})")

    async def check_deployer(self, address):
        address = Web3.to_checksum_address(address)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT reason FROM blacklist_deployer WHERE address = ?", (address,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def add_code_fingerprint(self, bytecode_hex, reason="Rug Code Similarity", address=None):
        """Add contract fingerprint to JSON blacklist"""
        fp_hash = self._calculate_fingerprint_hash(bytecode_hex)
        if not fp_hash:
            logger.warning("Could not extract fingerprint from bytecode")
            return

        if fp_hash in self.fingerprints:
            self.fingerprints[fp_hash]["rug_count"] += 1
            # Update example address if not set or just to keep latest
            if address:
                self.fingerprints[fp_hash]["example_address"] = address
        else:
            self.fingerprints[fp_hash] = {
                "first_seen": datetime.now().strftime("%Y-%m-%d"),
                "rug_count": 1,
                "example_address": address or "Unknown",
                "reason": reason
            }
        
        self._save_fingerprints()
        logger.info(f"Added code fingerprint: {fp_hash[:8]}... ({reason})")

    async def check_code_similarity(self, bytecode_hex, threshold=0.85): # threshold param kept for compatibility but not used for exact hash match
        """Check if bytecode fingerprint matches known rugs"""
        if not bytecode_hex or len(bytecode_hex) < 100:
            return None
            
        fp_hash = self._calculate_fingerprint_hash(bytecode_hex)
        if not fp_hash:
            return None
            
        if fp_hash in self.fingerprints:
            info = self.fingerprints[fp_hash]
            return f"Match Rug Fingerprint (Count: {info.get('rug_count', 0)})"
            
        return None

    def _calculate_similarity(self, s1, s2):
        """Calculate similarity ratio between two hex strings"""
        if s1 == s2:
            return 1.0
        # Simple difflib ratio
        import difflib
        return difflib.SequenceMatcher(None, s1, s2).ratio()
