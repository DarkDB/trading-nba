import { clsx } from "clsx";
import { twMerge } from "tailwind-merge"

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

export function formatDate(dateString) {
  if (!dateString) return '';
  const date = new Date(dateString);
  return date.toLocaleDateString('es-ES', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });
}

export function formatTime(dateString) {
  if (!dateString) return '';
  const date = new Date(dateString);
  return date.toLocaleTimeString('es-ES', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Europe/Madrid',
  });
}

export function formatDateTime(dateString) {
  if (!dateString) return '';
  const date = new Date(dateString);
  return date.toLocaleString('es-ES', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Europe/Madrid',
  });
}

export function formatSpread(spread) {
  if (spread === null || spread === undefined) return '-';
  const num = parseFloat(spread);
  return num > 0 ? `+${num.toFixed(1)}` : num.toFixed(1);
}

export function formatOdds(odds) {
  if (odds === null || odds === undefined) return '-';
  return parseFloat(odds).toFixed(2);
}

export function formatMargin(margin) {
  if (margin === null || margin === undefined) return '-';
  const num = parseFloat(margin);
  return num > 0 ? `+${num.toFixed(1)}` : num.toFixed(1);
}

export function getSignalColor(signal) {
  switch (signal?.toLowerCase()) {
    case 'green':
      return 'text-green-500';
    case 'yellow':
      return 'text-yellow-500';
    case 'red':
      return 'text-red-500';
    default:
      return 'text-zinc-400';
  }
}

export function getSignalBadgeClass(signal) {
  switch (signal?.toLowerCase()) {
    case 'green':
      return 'bg-green-500/10 text-green-500 border-green-500/20';
    case 'yellow':
      return 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20';
    case 'red':
      return 'bg-red-500/10 text-red-500 border-red-500/20';
    default:
      return 'bg-zinc-500/10 text-zinc-500 border-zinc-500/20';
  }
}

export function downloadBlob(blob, filename) {
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.URL.revokeObjectURL(url);
}
