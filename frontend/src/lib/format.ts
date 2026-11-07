// Labels, dates, sizes and kinds, worded as in DESIGN-HANDOFF.md and Finder.

const DAY_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const DAY_LONG = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
const MONTH_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const MONTH_LONG = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

export const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate())

export const sameDay = (a: Date, b: Date) => startOfDay(a).getTime() === startOfDay(b).getTime()

export function daysAgo(d: Date, now: Date): number {
  return Math.round((startOfDay(now).getTime() - startOfDay(d).getTime()) / 864e5)
}

export function clock(d: Date): string {
  const h = d.getHours()
  return `${h % 12 || 12}:${String(d.getMinutes()).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`
}

export const shortDate = (d: Date) => `${MONTH_SHORT[d.getMonth()]} ${d.getDate()}`

/** "Sun, Sep 27" */
export const shortDay = (d: Date) => `${DAY_SHORT[d.getDay()]}, ${shortDate(d)}`

/** "Today", "Yesterday", "Sun, Sep 27" */
export function dayName(d: Date, now: Date): string {
  const a = daysAgo(d, now)
  if (a === 0) return 'Today'
  if (a === 1) return 'Yesterday'
  return shortDay(d)
}

/** "Sunday, September 27, 2026" */
export function longDay(d: Date): string {
  return `${DAY_LONG[d.getDay()]}, ${MONTH_LONG[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`
}

export const monthYear = (d: Date) => `${MONTH_LONG[d.getMonth()]} ${d.getFullYear()}`

/** Finder's Date Modified: "Today at 11:02 AM", "Sep 12, 2026 at 2:00 PM" */
export function modifiedLabel(ms: number, now: Date): string {
  const d = new Date(ms)
  const a = daysAgo(d, now)
  const day = a === 0 ? 'Today' : a === 1 ? 'Yesterday' : `${shortDate(d)}, ${d.getFullYear()}`
  return `${day} at ${clock(d)}`
}

/** Decimal units as in Finder. Folders: "--". */
export function formatSize(bytes: number | null): string {
  if (bytes == null) return '--'
  if (bytes === 0) return 'Zero bytes'
  if (bytes < 1e3) return `${bytes} bytes`
  if (bytes < 1e6) return `${Math.round(bytes / 1e3)} KB`
  if (bytes < 1e9) return `${(bytes / 1e6).toFixed(1)} MB`
  return `${(bytes / 1e9).toFixed(2)} GB`
}

/** "34 files and 56 folders", "23 folders", "1 file"; empty when there's nothing. */
export function itemCount(files: number, folders: number): string {
  const part = (n: number, one: string) => `${n.toLocaleString('en-US')} ${one}${n === 1 ? '' : 's'}`
  if (files && folders) return `${part(files, 'file')} and ${part(folders, 'folder')}`
  if (folders) return part(folders, 'folder')
  if (files) return part(files, 'file')
  return ''
}

const KINDS: Record<string, string> = {
  jpg: 'JPEG image', jpeg: 'JPEG image', png: 'PNG image', gif: 'GIF image', webp: 'WebP image',
  tif: 'TIFF image', tiff: 'TIFF image', heic: 'HEIF image', heif: 'HEIF image', bmp: 'BMP image',
  svg: 'SVG image', dng: 'DNG image', nef: 'Nikon raw image', cr2: 'Canon raw image',
  cr3: 'Canon raw image', arw: 'Sony raw image', raf: 'Fujifilm raw image', orf: 'Olympus raw image',
  psd: 'Adobe Photoshop document', psb: 'Adobe Photoshop large document',
  ai: 'Adobe Illustrator document', indd: 'Adobe InDesign document', eps: 'EPS document',
  mov: 'QuickTime movie', mp4: 'MPEG-4 movie', m4v: 'Video', avi: 'AVI movie', mxf: 'MXF video',
  mkv: 'Matroska video', braw: 'Blackmagic RAW video', r3d: 'RED raw video',
  prproj: 'Adobe Premiere Pro project', aep: 'Adobe After Effects project',
  drp: 'DaVinci Resolve project',
  wav: 'Waveform audio', aif: 'AIFF audio', aiff: 'AIFF audio', mp3: 'MP3 audio',
  m4a: 'Apple MPEG-4 audio',
  pdf: 'PDF document', txt: 'Plain Text', rtf: 'Rich Text Document', md: 'Markdown document',
  csv: 'CSV document', doc: 'Microsoft Word document', docx: 'Microsoft Word document',
  xls: 'Microsoft Excel workbook', xlsx: 'Microsoft Excel workbook',
  ppt: 'Microsoft PowerPoint presentation', pptx: 'Microsoft PowerPoint presentation',
  key: 'Keynote Presentation', numbers: 'Numbers Spreadsheet', pages: 'Pages Document',
  zip: 'ZIP archive', rar: 'RAR archive', '7z': '7-Zip archive', dmg: 'Disk Image',
  html: 'HTML document', json: 'JSON document', xml: 'XML document',
  otf: 'OpenType font', ttf: 'TrueType font',
}

export function kindOf(name: string, dir: boolean): string {
  if (dir) return 'Folder'
  const dot = name.lastIndexOf('.')
  if (dot <= 0) return 'Document'
  return KINDS[name.slice(dot + 1).toLowerCase()] ?? 'Document'
}

export type IconKind = 'folder' | 'image' | 'video' | 'doc'

export function iconKind(kind: string): IconKind {
  if (kind === 'Folder') return 'folder'
  if (/image|Photoshop|Illustrator/.test(kind)) return 'image'
  if (/movie|video/i.test(kind)) return 'video'
  return 'doc'
}
