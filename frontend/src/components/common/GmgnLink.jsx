
import React from 'react';
import { ExternalLinkIcon } from 'lucide-react';

const GmgnLink = ({ address, className = "text-gray-400 hover:text-indigo-600" }) => {
  if (!address) return null;

  return (
    <a
      href={`https://gmgn.ai/bsc/token/${address}`}
      target="_blank"
      rel="noopener noreferrer"
      className={`inline-flex items-center ml-1.5 transition-colors ${className}`}
      title="在 GMGN.ai 上查看"
      onClick={(e) => e.stopPropagation()}
    >
      <span className="sr-only">GMGN</span>
      {/* Custom GMGN Icon or just generic external link */}
      <img 
        src="https://gmgn.ai/favicon.ico" 
        alt="GMGN" 
        className="w-3.5 h-3.5 opacity-70 hover:opacity-100"
        onError={(e) => {
            // Fallback to text/icon if favicon fails
            e.target.style.display = 'none';
            e.target.nextSibling.style.display = 'block';
        }}
      />
      <ExternalLinkIcon className="w-3 h-3 hidden" />
    </a>
  );
};

export default GmgnLink;
