import React, { Fragment } from 'react';
import { Dialog, Transition } from '@headlessui/react';
import { ProgressBar, Title, Text, Flex, Grid, Card, Metric } from '@tremor/react';
import { XIcon, ShieldCheckIcon, ExternalLinkIcon, AlertTriangleIcon, CheckCircleIcon, XCircleIcon } from 'lucide-react';
import { formatDate } from '../../utils/formatters';

// 从嵌套 raw_data 结构中提取平铺字段（与 Discoveries.jsx 保持一致）
const extractDetails = (raw = {}) => {
  const goplus = raw.goplus || {};
  const honeypot = raw.honeypot || {};
  const simulation = raw.simulation || {};
  const holders = raw.holders || {};
  const contract = raw.contract || {};

  const isHoneypot =
    simulation.is_honeypot === true ||
    goplus.is_honeypot === '1' ||
    honeypot.isHoneypot === true ||
    false;

  let buyTax = null;
  if (goplus.buy_tax != null) buyTax = parseFloat(goplus.buy_tax) * 100;
  else if (simulation.buy_tax != null) buyTax = parseFloat(simulation.buy_tax);
  else if (honeypot.simulationResult?.buyTax != null) buyTax = parseFloat(honeypot.simulationResult.buyTax);

  let sellTax = null;
  if (goplus.sell_tax != null) sellTax = parseFloat(goplus.sell_tax) * 100;
  else if (simulation.sell_tax != null) sellTax = parseFloat(simulation.sell_tax);
  else if (honeypot.simulationResult?.sellTax != null) sellTax = parseFloat(honeypot.simulationResult.sellTax);

  const holderConcentration = holders.top_5_share != null ? parseFloat(holders.top_5_share) : null;
  const isOpenSource = !!contract.SourceCode || goplus.is_open_source === '1';
  const isRenounced = goplus.owner_address === '0x0000000000000000000000000000000000000000';
  const lpLocked = holders.lp_locked === true || goplus.lp_locked === '1';

  return { isHoneypot, buyTax, sellTax, holderConcentration, isOpenSource, isRenounced, lpLocked };
};

const getScoreColor = (score) => {
  if (score >= 80) return 'emerald';
  if (score >= 60) return 'yellow';
  if (score >= 40) return 'orange';
  return 'rose';
};

const BoolRow = ({ label, value, goodWhenTrue = true }) => {
  const unknown = value === null || value === undefined;
  const isGood = unknown ? null : (goodWhenTrue ? value : !value);
  return (
    <div className="flex items-center justify-between py-2 border-b border-gray-100 last:border-0">
      <span className="text-sm text-gray-500">{label}</span>
      {unknown ? (
        <span className="text-sm text-gray-400">未知</span>
      ) : (
        <div className="flex items-center gap-1.5">
          {isGood
            ? <CheckCircleIcon className="w-4 h-4 text-emerald-500" />
            : <XCircleIcon className="w-4 h-4 text-rose-500" />
          }
          <span className={`text-sm font-medium ${isGood ? 'text-emerald-700' : 'text-rose-700'}`}>
            {value ? '是' : '否'}
          </span>
        </div>
      )}
    </div>
  );
};

const PctRow = ({ label, value }) => (
  <div className="flex items-center justify-between py-2 border-b border-gray-100 last:border-0">
    <span className="text-sm text-gray-500">{label}</span>
    <span className="text-sm font-medium text-gray-700">
      {value != null ? `${value.toFixed(1)}%` : '未知'}
    </span>
  </div>
);

const DiscoveryDrawer = ({ isOpen, closeModal, discovery }) => {
  if (!discovery) return null;

  const {
    token_name,
    token_symbol,
    token_address,
    pair_address,
    dex_name,
    discovery_time,
    initial_liquidity,
    security_score,
    analysis_result,
    risk_factors = [],
    check_details = {}
  } = discovery;

  const scoreColor = getScoreColor(security_score);
  const flat = extractDetails(check_details);

  return (
    <Transition.Root show={isOpen} as={Fragment}>
      <Dialog as="div" className="relative z-50" onClose={closeModal}>
        <Transition.Child
          as={Fragment}
          enter="ease-in-out duration-500" enterFrom="opacity-0" enterTo="opacity-100"
          leave="ease-in-out duration-500" leaveFrom="opacity-100" leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-gray-900/75 transition-opacity" />
        </Transition.Child>

        <div className="fixed inset-0 overflow-hidden">
          <div className="absolute inset-0 overflow-hidden">
            <div className="pointer-events-none fixed inset-y-0 right-0 flex max-w-full pl-10">
              <Transition.Child
                as={Fragment}
                enter="transform transition ease-in-out duration-500 sm:duration-700"
                enterFrom="translate-x-full" enterTo="translate-x-0"
                leave="transform transition ease-in-out duration-500 sm:duration-700"
                leaveFrom="translate-x-0" leaveTo="translate-x-full"
              >
                <Dialog.Panel className="pointer-events-auto w-screen max-w-2xl">
                  <div className="flex h-full flex-col overflow-y-scroll bg-white shadow-xl">
                    {/* Header */}
                    <div className="bg-indigo-700 px-4 py-6 sm:px-6">
                      <div className="flex items-start justify-between">
                        <div>
                          <Dialog.Title className="text-xl font-semibold leading-6 text-white">
                            {token_name || token_symbol} <span className="text-indigo-200">({token_symbol})</span>
                          </Dialog.Title>
                          <p className="mt-1 text-sm text-indigo-200 font-mono break-all">{token_address}</p>
                        </div>
                        <button
                          type="button"
                          className="ml-3 rounded-md bg-indigo-700 text-indigo-200 hover:text-white focus:outline-none"
                          onClick={closeModal}
                        >
                          <XIcon className="h-6 w-6" />
                        </button>
                      </div>
                    </div>

                    <div className="relative flex-1 px-4 py-6 sm:px-6 space-y-8">
                      {/* Security Score */}
                      <section>
                        <Title className="flex items-center gap-2 mb-4">
                          <ShieldCheckIcon className="w-5 h-5 text-gray-500" />
                          安全评分
                        </Title>
                        <Card decoration="top" decorationColor={scoreColor}>
                          <Flex>
                            <Text>综合得分</Text>
                            <Text className={`text-xl font-bold text-${scoreColor}-600`}>
                              {security_score}/100
                            </Text>
                          </Flex>
                          <ProgressBar value={security_score} color={scoreColor} className="mt-3" />
                        </Card>
                      </section>

                      {/* Check Details */}
                      <section>
                        <Title className="mb-4">安全检测详情</Title>
                        <Card>
                          <BoolRow label="貔貅检测（蜜罐）" value={flat.isHoneypot} goodWhenTrue={false} />
                          <BoolRow label="合约已开源" value={flat.isOpenSource} goodWhenTrue={true} />
                          <BoolRow label="权限已放弃" value={flat.isRenounced} goodWhenTrue={true} />
                          <BoolRow label="流动性已锁定" value={flat.lpLocked} goodWhenTrue={true} />
                          <PctRow label="买税" value={flat.buyTax} />
                          <PctRow label="卖税" value={flat.sellTax} />
                          <PctRow label="前5持仓集中度" value={flat.holderConcentration} />
                        </Card>
                      </section>

                      {/* Risk Factors */}
                      {risk_factors.length > 0 && (
                        <section>
                          <Title className="flex items-center gap-2 mb-4">
                            <AlertTriangleIcon className="w-5 h-5 text-orange-500" />
                            风险因素
                          </Title>
                          <div className="bg-orange-50 border border-orange-100 rounded-lg p-4 space-y-2">
                            {risk_factors.map((risk, index) => (
                              <div key={index} className="flex items-start gap-2">
                                <span className="text-orange-500 mt-0.5">•</span>
                                <span className="text-sm text-orange-800">{risk}</span>
                              </div>
                            ))}
                          </div>
                        </section>
                      )}

                      {/* Token Details */}
                      <section>
                        <Title className="mb-4">代币详情</Title>
                        <Grid numItems={2} className="gap-4">
                          <Card>
                            <Text>初始流动性</Text>
                            <Metric className="mt-1">{parseFloat(initial_liquidity || 0).toFixed(4)} BNB</Metric>
                          </Card>
                          <Card>
                            <Text>发现时间</Text>
                            <div className="mt-1 text-lg font-medium text-gray-900">
                              {formatDate(discovery_time)}
                            </div>
                          </Card>
                          <Card>
                            <Text>交易对地址</Text>
                            <Flex className="mt-1 gap-2">
                              <span className="text-sm font-mono truncate">{pair_address}</span>
                              <a href={`https://bscscan.com/address/${pair_address}`} target="_blank" rel="noreferrer">
                                <ExternalLinkIcon className="w-4 h-4 text-gray-400 hover:text-indigo-600" />
                              </a>
                            </Flex>
                          </Card>
                          <Card>
                            <Text>DEX</Text>
                            <div className="mt-1 text-lg font-medium text-gray-900">
                              {dex_name || 'PancakeSwap V2'}
                            </div>
                          </Card>
                        </Grid>
                      </section>

                      {/* Raw JSON */}
                      <section>
                        <Title className="mb-4">原始检测数据</Title>
                        <pre className="bg-gray-900 text-gray-100 p-4 rounded-lg overflow-x-auto text-xs font-mono">
                          {JSON.stringify(check_details, null, 2)}
                        </pre>
                      </section>
                    </div>
                  </div>
                </Dialog.Panel>
              </Transition.Child>
            </div>
          </div>
        </div>
      </Dialog>
    </Transition.Root>
  );
};

export default DiscoveryDrawer;
