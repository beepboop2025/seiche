import { useEffect, useState } from "react";
import {
  MONEY_MARKET_WATCH_KEY, changeMarketWatch, forgetMarketWatch,
  readMarketWatch, rememberMarketWatch,
} from "./moneyMarketWatchlist";

const storage = () => window.localStorage;

export function useMoneyMarketWatchlist() {
  const [watch, setWatch] = useState(() => readMarketWatch(storage));
  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (event.key !== MONEY_MARKET_WATCH_KEY && event.key !== null) return;
      setWatch(readMarketWatch(storage));
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);
  return {
    ...watch,
    change: (id: string, shouldWatch: boolean) => setWatch(previous => changeMarketWatch(previous, id, shouldWatch, storage)),
    remember: (enabled: boolean) => setWatch(previous => enabled ? rememberMarketWatch(previous, storage) : forgetMarketWatch(previous, storage)),
    clear: () => setWatch(previous => forgetMarketWatch(previous, storage, true)),
  };
}
