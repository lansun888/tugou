import React, { Fragment } from 'react';
import { Dialog, Transition } from '@headlessui/react';
import { Button } from '@tremor/react';
import { AlertTriangle } from 'lucide-react';

const SellModal = ({ isOpen, closeModal, onConfirm, tokenName, percentage }) => {
  return (
    <Transition appear show={isOpen} as={Fragment}>
      <Dialog as="div" className="relative z-50" onClose={closeModal}>
        <Transition.Child
          as={Fragment}
          enter="ease-out duration-300"
          enterFrom="opacity-0"
          enterTo="opacity-100"
          leave="ease-in duration-200"
          leaveFrom="opacity-100"
          leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-black/70" />
        </Transition.Child>

        <div className="fixed inset-0 overflow-y-auto">
          <div className="flex min-h-full items-center justify-center p-4 text-center">
            <Transition.Child
              as={Fragment}
              enter="ease-out duration-300"
              enterFrom="opacity-0 scale-95"
              enterTo="opacity-100 scale-100"
              leave="ease-in duration-200"
              leaveFrom="opacity-100 scale-100"
              leaveTo="opacity-0 scale-95"
            >
              <Dialog.Panel className="w-full max-w-md transform overflow-hidden rounded-2xl bg-slate-900 border border-slate-700 p-6 text-left align-middle shadow-xl transition-all">
                <div className="flex items-center gap-3 mb-4">
                  <div className="p-2 bg-amber-500/20 rounded-full">
                    <AlertTriangle className="w-6 h-6 text-amber-500" />
                  </div>
                  <Dialog.Title
                    as="h3"
                    className="text-lg font-medium leading-6 text-white"
                  >
                    确认卖出
                  </Dialog.Title>
                </div>
                
                <div className="mt-2">
                  <p className="text-sm text-slate-300">
                    您确定要卖出 <span className="font-bold text-white">{tokenName}</span> 的 <span className="font-bold text-white">{percentage}%</span> 吗？
                  </p>
                  <p className="text-xs text-slate-400 mt-2">
                    此操作不可撤销。交易将立即提交。
                  </p>
                </div>

                <div className="mt-6 flex justify-end gap-3">
                  <Button 
                    variant="secondary" 
                    color="slate"
                    onClick={closeModal}
                  >
                    取消
                  </Button>
                  <Button 
                    variant="primary" 
                    color="rose"
                    onClick={onConfirm}
                  >
                    确认卖出
                  </Button>
                </div>
              </Dialog.Panel>
            </Transition.Child>
          </div>
        </div>
      </Dialog>
    </Transition>
  );
};

export default SellModal;
