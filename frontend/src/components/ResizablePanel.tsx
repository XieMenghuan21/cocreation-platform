import React, { useCallback, useEffect, useRef, useState } from 'react';

export interface ResizablePanelProps {
  left?: React.ReactNode | null;
  center: React.ReactNode;
  right?: React.ReactNode | null;
  leftWidth: number;
  rightWidth?: number;
  onLeftWidthChange: (width: number) => void;
  onRightWidthChange?: (width: number) => void;
  minLeft?: number;
  maxLeft?: number;
  minRight?: number;
  maxRight?: number;
}

export const ResizablePanel: React.FC<ResizablePanelProps> = ({
  left,
  center,
  right,
  leftWidth,
  rightWidth,
  onLeftWidthChange,
  onRightWidthChange,
  minLeft = 0,
  maxLeft = 400,
  minRight = 0,
  maxRight = 520,
}) => {
  const [dragging, setDragging] = useState<'left' | 'right' | null>(null);
  const startX = useRef(0);
  const startWidth = useRef(0);

  const showLeft = left != null;
  const showRight = right != null;
  const rightW = rightWidth ?? 0;
  const onRightW = onRightWidthChange ?? (() => {});

  const handleMouseDown = useCallback(
    (side: 'left' | 'right') => (e: React.MouseEvent) => {
      e.preventDefault();
      startX.current = e.clientX;
      startWidth.current = side === 'left' ? leftWidth : rightW;
      setDragging(side);
    },
    [leftWidth, rightW],
  );

  useEffect(() => {
    if (!dragging) return;

    function onMove(e: MouseEvent) {
      const delta = e.clientX - startX.current;
      if (dragging === 'left') {
        const w = Math.min(maxLeft, Math.max(minLeft, startWidth.current + delta));
        onLeftWidthChange(w);
      } else {
        const w = Math.min(maxRight, Math.max(minRight, startWidth.current - delta));
        onRightW(w);
      }
    }

    function onUp() {
      setDragging(null);
    }

    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    return () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
  }, [dragging, minLeft, maxLeft, minRight, maxRight, onLeftWidthChange, onRightW]);

  return (
    <div className="flex h-full min-w-0 flex-1">
      {showLeft ? (
        <>
          <div style={{ width: leftWidth }} className="h-full shrink-0 overflow-hidden">
            {left}
          </div>
          <div
            onMouseDown={handleMouseDown('left')}
            className={`h-full w-1 shrink-0 cursor-col-resize transition-colors ${dragging === 'left' ? 'bg-purple-500' : 'bg-slate-200 hover:bg-purple-300'}`}
          />
        </>
      ) : null}

      <div className="h-full min-w-0 flex-1">{center}</div>

      {showRight ? (
        <>
          <div
            onMouseDown={handleMouseDown('right')}
            className={`h-full w-1 shrink-0 cursor-col-resize transition-colors ${dragging === 'right' ? 'bg-purple-500' : 'bg-slate-200 hover:bg-purple-300'}`}
          />
          <div style={{ width: rightW }} className="h-full shrink-0 overflow-hidden">
            {right}
          </div>
        </>
      ) : null}
    </div>
  );
};
