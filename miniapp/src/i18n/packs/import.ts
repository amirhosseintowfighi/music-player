import { definePack } from '@/i18n/pack';

export const importStrings = definePack(
  {
    'import.title': 'وارد کردن پلی‌لیست',
    'import.hint': 'لیست آهنگ‌ها را بچسبان، هر خط یک آهنگ (مثلاً «ابی - خانه»). از اسپاتیفای؟ با ابزاری مثل Exportify خروجی CSV بگیر و فایلش را این‌جا بده.',
    'import.placeholder': 'ابی - خانه\nگوگوش - پل\n…',
    'import.file': 'انتخاب فایل CSV',
    'import.find': 'پیدا کردن آهنگ‌ها',
    'import.result': '{matched} از {total} آهنگ پیدا شد',
    'import.skipped': '{count} خط فقط لینک بود و رد شد.',
    'import.notFound': 'پیدا نشد',
    'import.name': 'نام پلی‌لیست',
    'import.defaultName': 'پلی‌لیست واردشده',
    'import.create': 'ساختن پلی‌لیست ({count})',
    'import.back': 'ویرایش لیست',
    'import.done': 'پلی‌لیست ساخته شد',
  },
  {
    'import.title': 'Import a playlist',
    'import.hint': 'Paste your songs, one per line (e.g. "Ebi - Khaneh"). From Spotify? Export a CSV with a tool such as Exportify and pick the file here.',
    'import.placeholder': 'Ebi - Khaneh\nGoogoosh - Pol\n…',
    'import.file': 'Pick a CSV file',
    'import.find': 'Find the songs',
    'import.result': 'Found {matched} of {total} songs',
    'import.skipped': '{count} lines were only links and were skipped.',
    'import.notFound': 'Not found',
    'import.name': 'Playlist name',
    'import.defaultName': 'Imported playlist',
    'import.create': 'Create playlist ({count})',
    'import.back': 'Edit the list',
    'import.done': 'Playlist created',
  },
);
